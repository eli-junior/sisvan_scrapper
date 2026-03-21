"""
Script de validação e consolidação dos dados SISVAN - pasta por_sexo.

Para cada arquivo sexo_YYYY_SEXO.xls:
  - Valida: ano, sexo, mês=TODOS, abrangência=BRASIL,
            tipo=Consumo de Alimentos Ultraprocessados,
            faixa etária=Crianças de 5 a 9 anos
  - Extrai: Total, %, acompanhados(as)
  - Registra divergências e ignora arquivos inválidos
  - Na próxima execução, preenche apenas os dados faltantes no consolidado

Saída: dados/consolidado_por_sexo.xlsx
"""

import re
import sys
import logging
from pathlib import Path
from html.parser import HTMLParser

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent / "dados"
SOURCE_DIR = BASE_DIR / "por_sexo"
OUTPUT_FILE = BASE_DIR / "consolidado_por_sexo.xlsx"
LOG_FILE = Path(__file__).parent / "logs" / "validate_por_sexo.log"

ANOS = list(range(2015, 2025))
SEXOS = ["FEMININO", "MASCULINO"]

# Strings esperadas dentro do HTML (case-insensitive strip)
EXPECT_MES = "TODOS"
EXPECT_ABRANGENCIA = "BRASIL"
EXPECT_TIPO = "consumo de alimentos ultraprocessados"
EXPECT_FAIXA = "crianças de 5 a 9 anos"
EXPECT_FASE_VIDA = "criança"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parser HTML para extrair metadados e dados da tabela
# ---------------------------------------------------------------------------

class SisvanParser(HTMLParser):
    """Extrai informações de metadados e dados do relatório HTML do SISVAN."""

    def __init__(self):
        super().__init__()
        self._in_box_body = False
        self._in_strong = False
        self._last_strong = None
        self._box_body_depth = 0
        self._depth = 0
        self._collecting_meta = False

        # Metadados extraídos do box-header
        self.meta_ano: str | None = None
        self.meta_mes: str | None = None
        self.meta_fase_vida: str | None = None
        self.meta_sexo: str | None = None

        # Campos ocultos do form (complemento)
        self.form_ano: str | None = None
        self.form_mes: str | None = None      # "99" = TODOS
        self.form_filtro: str | None = None   # "F" = BRASIL

        # Cabeçalhos e dados da tabela
        self.thead_texts: list[str] = []
        self.tbody_cells: list[str] = []   # células da linha BRASIL

        # Estado de parseamento da tabela
        self._in_thead = False
        self._in_tbody = False
        self._in_td_th = False
        self._in_tr = False
        self._current_cell = []
        self._brasil_row_found = False
        self._next_row_is_brasil = False
        self._current_row_cells: list[str] = []

    # ---- helpers -----------------------------------------------------------

    def _clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    # ---- HTMLParser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "")

        # Detectar box-body (metadados dos filtros)
        if tag == "div" and "box-body" in classes:
            self._collecting_meta = True

        if tag == "strong" and self._collecting_meta:
            self._in_strong = True
            self._current_cell = []

        # Tabela
        if tag == "thead":
            self._in_thead = True
        if tag == "tbody":
            self._in_tbody = True
        if tag in ("td", "th"):
            self._in_td_th = True
            self._current_cell = []
        if tag == "tr" and self._in_tbody:
            self._in_tr = True
            self._current_row_cells = []

        # Campos ocultos do formulário
        if tag == "input" and attr_dict.get("type") == "hidden":
            name = attr_dict.get("name", "")
            value = attr_dict.get("value", "")
            if name == "nuAno":
                self.form_ano = value
            elif name == "nuMes[]":
                self.form_mes = value
            elif name == "tpFiltro":
                self.form_filtro = value

    def handle_endtag(self, tag):
        self._depth -= 1

        if tag == "strong" and self._in_strong:
            self._in_strong = False
            text = self._clean("".join(self._current_cell))
            self._last_strong = text

        if tag in ("td", "th") and self._in_td_th:
            self._in_td_th = False
            text = self._clean("".join(self._current_cell))
            if self._in_thead:
                if text:
                    self.thead_texts.append(text)
            elif self._in_tbody and self._in_tr:
                self._current_row_cells.append(text)

        if tag == "tr" and self._in_tbody and self._in_tr:
            self._in_tr = False
            row_text = " ".join(self._current_row_cells)
            if "BRASIL" in row_text.upper() and not self._brasil_row_found:
                self._brasil_row_found = True
                self.tbody_cells = self._current_row_cells[:]

        if tag == "thead":
            self._in_thead = False
        if tag == "tbody":
            self._in_tbody = False

    def handle_data(self, data):
        if self._in_strong:
            self._current_cell.append(data)
        elif self._in_td_th:
            self._current_cell.append(data)
        elif self._collecting_meta and not self._in_td_th:
            # Texto livre dentro do box-body após um <strong>
            text = data.strip()
            if text and self._last_strong:
                label = self._last_strong.rstrip(":").strip().upper()
                if label == "ANO":
                    # "2015 - " → extrair só o ano
                    m = re.search(r"(\d{4})", text)
                    if m:
                        self.meta_ano = m.group(1)
                elif label == "MÊS":
                    self.meta_mes = text.strip(" -")
                elif label == "FASE DA VIDA":
                    self.meta_fase_vida = text
                elif label == "SEXO":
                    self.meta_sexo = text
                self._last_strong = None  # consumido


# ---------------------------------------------------------------------------
# Funções de validação
# ---------------------------------------------------------------------------

def parse_xls(filepath: Path) -> SisvanParser:
    """Lê o arquivo HTML (com extensão .xls) e faz parse."""
    with open(filepath, "rb") as f:
        raw = f.read()
    # Detectar encoding
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        content = raw.decode("utf-8", errors="replace")

    parser = SisvanParser()
    parser.feed(content)
    return parser


def validar_arquivo(filepath: Path, ano_esperado: int, sexo_esperado: str) -> dict | None:
    """
    Valida o arquivo e retorna um dict com os dados extraídos, ou None em caso de erro.

    dict keys: ano, sexo, total, percentual, acompanhados, divergencias
    """
    divergencias = []
    parser = parse_xls(filepath)

    # --- Validar ANO ---
    ano_ok = False
    ano_encontrado = parser.meta_ano or parser.form_ano
    if ano_encontrado:
        if str(ano_esperado) == str(ano_encontrado).strip():
            ano_ok = True
        else:
            divergencias.append(
                f"ANO: esperado {ano_esperado}, encontrado '{ano_encontrado}'"
            )
    else:
        divergencias.append("ANO: não encontrado no arquivo")

    # --- Validar SEXO ---
    sexo_ok = False
    sexo_encontrado = parser.meta_sexo
    if sexo_encontrado:
        if sexo_esperado.upper() == sexo_encontrado.upper():
            sexo_ok = True
        else:
            divergencias.append(
                f"SEXO: esperado '{sexo_esperado}', encontrado '{sexo_encontrado}'"
            )
    else:
        divergencias.append("SEXO: não encontrado no arquivo")

    # --- Validar MÊS = TODOS ---
    mes_ok = False
    mes_encontrado = parser.meta_mes or ("TODOS" if parser.form_mes == "99" else parser.form_mes)
    if mes_encontrado:
        if EXPECT_MES.upper() in str(mes_encontrado).upper():
            mes_ok = True
        else:
            divergencias.append(
                f"MÊS: esperado '{EXPECT_MES}', encontrado '{mes_encontrado}'"
            )
    else:
        divergencias.append("MÊS: não encontrado no arquivo")

    # --- Validar ABRANGÊNCIA = BRASIL ---
    brasil_ok = parser._brasil_row_found
    if not brasil_ok:
        # Verificar também pelo form (tpFiltro=F significa BRASIL)
        if parser.form_filtro == "F":
            brasil_ok = True
        else:
            divergencias.append("ABRANGÊNCIA: linha BRASIL não encontrada na tabela")

    # --- Validar TIPO = Consumo de Alimentos Ultraprocessados ---
    tipo_ok = any(
        EXPECT_TIPO in t.lower() for t in parser.thead_texts
    )
    if not tipo_ok:
        divergencias.append(
            f"TIPO: 'Consumo de Alimentos Ultraprocessados' não encontrado nos cabeçalhos da tabela"
        )

    # --- Validar FAIXA ETÁRIA = Crianças de 5 a 9 anos ---
    faixa_ok = any(
        EXPECT_FAIXA in t.lower() for t in parser.thead_texts
    )
    if not faixa_ok:
        divergencias.append(
            f"FAIXA ETÁRIA: 'Crianças de 5 a 9 anos' não encontrado nos cabeçalhos da tabela"
        )

    # --- Extrair dados da linha BRASIL ---
    # A linha tem: [BRASIL, Total, %, acompanhados]  (primeiras cols são BRASIL repetida 3x no colspan)
    # tbody_cells = células da linha BRASIL sem repetição: ['BRASIL', '17254', '87%', '19.742']
    total = None
    percentual = None
    acompanhados = None

    cells = parser.tbody_cells
    # Remover a célula "BRASIL" e pegar as 3 numéricas seguintes
    numeric_cells = [c for c in cells if c.upper() != "BRASIL"]
    if len(numeric_cells) >= 3:
        total = numeric_cells[0]
        percentual = numeric_cells[1]
        acompanhados = numeric_cells[2]
    elif len(numeric_cells) == 2:
        total = numeric_cells[0]
        percentual = numeric_cells[1]
        divergencias.append("DADOS: apenas 2 valores numéricos encontrados (faltou acompanhados)")
    elif len(numeric_cells) == 1:
        total = numeric_cells[0]
        divergencias.append("DADOS: apenas 1 valor numérico encontrado")
    else:
        divergencias.append("DADOS: nenhum valor numérico encontrado na linha BRASIL")

    return {
        "ano": ano_esperado,
        "sexo": sexo_esperado,
        "total": total,
        "percentual": percentual,
        "acompanhados": acompanhados,
        "divergencias": divergencias,
        "valido": len(divergencias) == 0,
    }


# ---------------------------------------------------------------------------
# Consolidação no Excel
# ---------------------------------------------------------------------------

def carregar_consolidado_existente(output_file: Path) -> dict[tuple, dict]:
    """
    Lê o consolidado existente e retorna um dict indexado por (ano, sexo).
    Retorna dict vazio se o arquivo não existir.
    """
    existentes = {}
    if not output_file.exists():
        return existentes

    try:
        wb = openpyxl.load_workbook(output_file)
        ws = wb.active
        # Pular as 2 linhas de cabeçalho
        for row in ws.iter_rows(min_row=3, values_only=True):
            if row[0] is None:
                continue
            ano, sexo, total, pct, acomp = row[0], row[1], row[2], row[3], row[4]
            if ano and sexo:
                existentes[(int(ano), str(sexo).upper())] = {
                    "total": total,
                    "percentual": pct,
                    "acompanhados": acomp,
                }
    except Exception as e:
        log.warning(f"Não foi possível ler o consolidado existente: {e}")

    return existentes


def criar_workbook() -> tuple:
    """Cria e estiliza o workbook de saída."""
    wb = Workbook()
    ws = wb.active
    ws.title = "por_sexo"

    # Estilos
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="2E7D32")  # verde escuro
    subheader_fill = PatternFill("solid", fgColor="A5D6A7")  # verde claro
    center = Alignment(horizontal="center", vertical="center")
    wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="9E9E9E")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Linha 1: título
    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = "SISVAN – Consumo de Alimentos Ultraprocessados | Crianças de 5 a 9 anos | Agrupado por: BRASIL"
    title_cell.font = Font(name="Calibri", bold=True, color="FFFFFF", size=12)
    title_cell.fill = PatternFill("solid", fgColor="1B5E20")
    title_cell.alignment = center
    ws.row_dimensions[1].height = 22

    # Linha 2: cabeçalhos
    headers = ["Ano", "Sexo", "Total (Ultraprocessados)", "% (Ultraprocessados)", "Total Acompanhados(as)"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = wrap
        cell.border = border
    ws.row_dimensions[2].height = 30

    # Larguras das colunas
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 24

    return wb, ws


def escrever_dados(ws, row_num: int, ano: int, sexo: str, total, percentual, acompanhados):
    """Escreve uma linha de dados no worksheet."""
    thin = Side(style="thin", color="9E9E9E")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")

    fill_fem = PatternFill("solid", fgColor="FCE4EC")   # rosa claro para FEMININO
    fill_masc = PatternFill("solid", fgColor="E3F2FD")  # azul claro para MASCULINO
    fill = fill_fem if sexo.upper() == "FEMININO" else fill_masc

    values = [ano, sexo, total, percentual, acompanhados]
    for col, val in enumerate(values, start=1):
        cell = ws.cell(row=row_num, column=col, value=val)
        cell.alignment = center
        cell.border = border
        cell.fill = fill


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("SISVAN - Validação e Consolidação: por_sexo")
    log.info("=" * 60)

    if not SOURCE_DIR.exists():
        log.error(f"Pasta de origem não encontrada: {SOURCE_DIR}")
        sys.exit(1)

    # Carregar dados já presentes no consolidado (para pular os que já existem)
    existentes = carregar_consolidado_existente(OUTPUT_FILE)
    log.info(f"Consolidado existente: {len(existentes)} registros já preenchidos")

    # Criar workbook (novo ou do zero) com estrutura completa
    # A estratégia é reescrever o arquivo inteiro, preservando os dados existentes
    # e adicionando os novos validados. Dados inválidos são ignorados.
    wb, ws = criar_workbook()

    total_arquivos = len(ANOS) * len(SEXOS)
    processados = 0
    validos = 0
    com_divergencias = 0
    faltando = 0
    pulados = 0

    thin = Side(style="thin", color="9E9E9E")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    row_num = 3
    divergencias_report = []

    for ano in ANOS:
        for sexo in SEXOS:
            processados += 1
            chave = (ano, sexo.upper())
            nome_arquivo = f"sexo_{ano}_{sexo}.xls"
            filepath = SOURCE_DIR / nome_arquivo

            # Verificar se o arquivo existe
            if not filepath.exists():
                log.warning(f"  [{processados}/{total_arquivos}] FALTANDO: {nome_arquivo}")
                faltando += 1
                # Escrever linha vazia para manter a estrutura
                escrever_dados(ws, row_num, ano, sexo, "ARQUIVO FALTANDO", None, None)
                row_num += 1
                continue

            # Se já existe no consolidado E o arquivo também existe (dados ok antes),
            # podemos re-usar os dados existentes sem revalidar o arquivo
            if chave in existentes:
                dados_ex = existentes[chave]
                if dados_ex["total"] not in (None, "INVÁLIDO", "ARQUIVO FALTANDO"):
                    log.info(f"  [{processados}/{total_arquivos}] JÁ PROCESSADO: {nome_arquivo} (reutilizando)")
                    pulados += 1
                    escrever_dados(
                        ws, row_num, ano, sexo,
                        dados_ex["total"], dados_ex["percentual"], dados_ex["acompanhados"]
                    )
                    row_num += 1
                    continue

            # Validar o arquivo
            log.info(f"  [{processados}/{total_arquivos}] Validando: {nome_arquivo}")
            resultado = validar_arquivo(filepath, ano, sexo)

            if resultado["valido"]:
                validos += 1
                log.info(
                    f"    ✓ OK | Total={resultado['total']} | "
                    f"%={resultado['percentual']} | "
                    f"Acomp={resultado['acompanhados']}"
                )
                escrever_dados(
                    ws, row_num, ano, sexo,
                    resultado["total"], resultado["percentual"], resultado["acompanhados"]
                )
            else:
                com_divergencias += 1
                log.warning(f"    ✗ DIVERGÊNCIAS em {nome_arquivo}:")
                for d in resultado["divergencias"]:
                    log.warning(f"      - {d}")
                divergencias_report.append((nome_arquivo, resultado["divergencias"]))
                # Registrar como inválido na planilha
                escrever_dados(ws, row_num, ano, sexo, "INVÁLIDO", None, None)
                # Marcar em vermelho
                red_fill = PatternFill("solid", fgColor="FFCDD2")
                for col in range(1, 6):
                    ws.cell(row=row_num, column=col).fill = red_fill

            row_num += 1

    # Salvar
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT_FILE)

    # Resumo
    log.info("")
    log.info("=" * 60)
    log.info("RESUMO")
    log.info("=" * 60)
    log.info(f"  Arquivos esperados  : {total_arquivos}")
    log.info(f"  Faltando            : {faltando}")
    log.info(f"  Já processados      : {pulados}")
    log.info(f"  Validados agora     : {validos}")
    log.info(f"  Com divergências    : {com_divergencias}")
    log.info(f"  Consolidado salvo em: {OUTPUT_FILE.resolve()}")

    if divergencias_report:
        log.info("")
        log.info("ARQUIVOS COM DIVERGÊNCIAS:")
        for nome, divs in divergencias_report:
            log.warning(f"  {nome}:")
            for d in divs:
                log.warning(f"    - {d}")

    return com_divergencias


if __name__ == "__main__":
    main()
