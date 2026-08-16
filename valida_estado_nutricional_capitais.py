"""Valida os relatórios SISVAN de Estado Nutricional de adultos por capital.

Confere filtros/metadados, a identidade IBGE da capital e a consistência das
seis classificações de IMC. Os arquivos-fonte nunca são alterados.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sisvan_capitais import CAPITAIS, Capital, slug


RAIZ = Path(__file__).parent
BASE_DIR = RAIZ / "dados" / "estado_nutricional_adultos_capitais"
LOG_FILE = RAIZ / "logs" / "valida_estado_nutricional_capitais.log"
REPORT_FILE = BASE_DIR / "relatorio_validacao.json"
ANOS = tuple(range(2015, 2025))

CLASSIFICACOES = (
    "Baixo peso",
    "Adequado ou Eutrófico",
    "Sobrepeso",
    "Obesidade Grau I",
    "Obesidade Grau II",
    "Obesidade Grau III",
)


def normalizar(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acentos).strip().upper()


class RelatorioParser(HTMLParser):
    """Extrai o texto visível e as linhas da tabela #relatorio."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.textos: list[str] = []
        self.cabecalhos: list[str] = []
        self.linhas: list[list[str]] = []
        self._na_tabela = False
        self._no_thead = False
        self._no_tbody = False
        self._na_linha = False
        self._na_celula = False
        self._celula: list[str] = []
        self._linha: list[str] = []

    @staticmethod
    def _limpar(partes: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(partes)).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        if tag == "table" and atributos.get("id") == "relatorio":
            self._na_tabela = True
        if not self._na_tabela:
            return
        if tag == "thead":
            self._no_thead = True
        elif tag == "tbody":
            self._no_tbody = True
        elif tag == "tr":
            self._na_linha = True
            self._linha = []
        elif tag in {"td", "th"}:
            self._na_celula = True
            self._celula = []

    def handle_endtag(self, tag: str) -> None:
        if not self._na_tabela:
            return
        if tag in {"td", "th"} and self._na_celula:
            valor = self._limpar(self._celula)
            self._linha.append(valor)
            if self._no_thead and valor:
                self.cabecalhos.append(valor)
            self._na_celula = False
        elif tag == "tr" and self._na_linha:
            if self._no_tbody and any(self._linha):
                self.linhas.append(self._linha[:])
            self._na_linha = False
        elif tag == "thead":
            self._no_thead = False
        elif tag == "tbody":
            self._no_tbody = False
        elif tag == "table":
            self._na_tabela = False

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.textos.append(data)
            if self._na_celula:
                self._celula.append(data)

    @property
    def texto(self) -> str:
        return self._limpar(self.textos)


def ler_html(caminho: Path) -> tuple[str, RelatorioParser]:
    bruto = caminho.read_bytes()
    if bruto.startswith(b"PK"):
        raise ValueError("arquivo XLSX binário inesperado; era esperado o HTML/XLS do SISVAN")
    conteudo: str | None = None
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            conteudo = bruto.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if conteudo is None:
        conteudo = bruto.decode("utf-8", errors="replace")
    parser = RelatorioParser()
    parser.feed(conteudo)
    return conteudo, parser


def extrair_meta(texto: str, rotulo: str, proximo: str | None = None) -> str | None:
    fim = rf"(?=\s+{re.escape(proximo)}\s*:)" if proximo else r"(?=<|$)"
    padrao = rf"{re.escape(rotulo)}\s*:\s*(.+?){fim}"
    match = re.search(padrao, texto, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1)).strip(" -") if match else None


def inteiro_br(valor: str) -> int:
    texto = valor.strip().replace(".", "").replace(" ", "")
    if not re.fullmatch(r"\d+", texto):
        raise ValueError(f"quantidade inválida: {valor!r}")
    return int(texto)


def percentual_br(valor: str) -> float:
    texto = valor.strip().rstrip("%").replace(",", ".")
    return float(texto)


def validar_arquivo(caminho: Path, ano: int, capital: Capital) -> dict[str, Any]:
    divergencias: list[str] = []
    nome_esperado = nome_base(ano, capital)
    registro: dict[str, Any] = {
        "arquivo": str(caminho.relative_to(BASE_DIR)),
        "nome_esperado": f"{nome_esperado}{caminho.suffix.lower()}",
        "ano_esperado": ano,
        "uf_esperada": capital.uf,
        "capital_esperada": capital.nome,
        "codigo_ibge_esperado": capital.codigo_municipio,
    }
    if caminho.stem != nome_esperado:
        divergencias.append(
            f"NOME DO ARQUIVO: esperado '{nome_esperado}', encontrado '{caminho.stem}'"
        )
    try:
        _, parser = ler_html(caminho)
    except Exception as exc:
        registro.update(status="INVALIDO", divergencias=[f"LEITURA: {exc}"])
        return registro

    texto = parser.texto
    texto_normalizado = normalizar(texto)
    meta_ano = extrair_meta(texto, "Ano", "Mês")
    meta_mes = extrair_meta(texto, "Mês", "Fase da Vida")
    meta_fase = extrair_meta(texto, "Fase da Vida", "Sexo")
    match_sexo = re.search(r"\bSexo\s*:\s*([^\s<]+)", texto, flags=re.IGNORECASE)
    meta_sexo = match_sexo.group(1).strip(" -") if match_sexo else None
    registro["metadados_encontrados"] = {
        "ano": meta_ano,
        "mes": meta_mes,
        "fase_da_vida": meta_fase,
        "sexo": meta_sexo,
    }

    if "ESTADO NUTRICIONAL" not in texto_normalizado:
        divergencias.append("RELATÓRIO: texto 'Estado nutricional' não encontrado")
    if not meta_ano or not re.search(rf"\b{ano}\b", meta_ano):
        divergencias.append(
            f"ANO x NOME DO ARQUIVO: nome informa {ano}, conteúdo informa {meta_ano!r}"
        )
    if normalizar(meta_mes or "") != "TODOS":
        divergencias.append(f"MÊS: esperado 'TODOS', encontrado {meta_mes!r}")
    if normalizar(meta_fase or "") != "ADULTO":
        divergencias.append(f"FASE DA VIDA: esperado 'ADULTO', encontrado {meta_fase!r}")
    if normalizar(meta_sexo or "") != "TODOS":
        divergencias.append(f"SEXO: esperado 'TODOS', encontrado {meta_sexo!r}")

    cabecalho = " | ".join(normalizar(c) for c in parser.cabecalhos)
    if "IMC" not in cabecalho:
        divergencias.append("ÍNDICE: cabeçalho IMC não encontrado")
    for classificacao in CLASSIFICACOES:
        if normalizar(classificacao) not in cabecalho:
            divergencias.append(f"CLASSIFICAÇÃO: '{classificacao}' não encontrada")

    linhas_municipais = [
        linha for linha in parser.linhas
        if len(linha) >= 18 and linha[1].strip().isdigit() and linha[3].strip().isdigit()
    ]
    if len(linhas_municipais) != 1:
        divergencias.append(
            f"DADOS: esperada 1 linha municipal, encontradas {len(linhas_municipais)}"
        )

    linha = next(
        (linha for linha in linhas_municipais if linha[3].strip() == capital.codigo_municipio),
        linhas_municipais[0] if linhas_municipais else None,
    )
    if linha:
        encontrado = {
            "regiao": linha[0], "codigo_uf": linha[1], "uf": linha[2],
            "codigo_ibge": linha[3], "municipio": linha[4],
        }
        registro["municipio_encontrado"] = encontrado
        comparacoes = (
            ("CÓDIGO UF", capital.codigo_uf, linha[1].strip()),
            ("UF", capital.uf, linha[2].strip()),
            ("CÓDIGO IBGE", capital.codigo_municipio, linha[3].strip()),
            ("MUNICÍPIO", normalizar(capital.nome), normalizar(linha[4])),
        )
        for rotulo, esperado, obtido in comparacoes:
            if esperado != obtido:
                prefixo = "MUNICÍPIO x NOME DO ARQUIVO" if rotulo in {"CÓDIGO IBGE", "MUNICÍPIO"} else rotulo
                divergencias.append(f"{prefixo}: esperado {esperado!r}, encontrado {obtido!r}")

        metricas = linha[5:18]
        if len(metricas) != 13:
            divergencias.append(f"DADOS: esperadas 13 métricas de IMC, encontradas {len(metricas)}")
        else:
            try:
                quantidades = [inteiro_br(metricas[i]) for i in range(0, 12, 2)]
                percentuais = [percentual_br(metricas[i]) for i in range(1, 12, 2)]
                total = inteiro_br(metricas[12])
                registro["dados"] = {
                    "quantidades": dict(zip(CLASSIFICACOES, quantidades)),
                    "percentuais": dict(zip(CLASSIFICACOES, percentuais)),
                    "total": total,
                    "soma_quantidades": sum(quantidades),
                    "soma_percentuais": round(sum(percentuais), 4),
                }
                if sum(quantidades) != total:
                    divergencias.append(
                        f"TOTAL: soma das categorias={sum(quantidades)}, total informado={total}"
                    )
                if total > 0 and abs(sum(percentuais) - 100.0) > 0.15:
                    divergencias.append(
                        f"PERCENTUAIS: soma esperada ~100%, encontrada {sum(percentuais):.2f}%"
                    )
            except ValueError as exc:
                divergencias.append(f"DADOS: {exc}")

    registro["divergencias"] = divergencias
    registro["status"] = "VALIDO" if not divergencias else "INVALIDO"
    return registro


def nome_base(ano: int, capital: Capital) -> str:
    return f"estado_nutricional_adulto_{ano}_{capital.uf}_{slug(capital.nome)}"


def localizar_arquivo(ano: int, capital: Capital) -> tuple[Path | None, list[Path]]:
    base = BASE_DIR / capital.uf / nome_base(ano, capital)
    encontrados = [base.with_suffix(ext) for ext in (".xls", ".xlsx") if base.with_suffix(ext).exists()]
    return (encontrados[0] if encontrados else None), encontrados


def configurar_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida Estado Nutricional de adultos por capital.")
    parser.add_argument("--anos", nargs="+", type=int, default=list(ANOS))
    parser.add_argument("--ufs", nargs="+", type=str.upper, default=[c.uf for c in CAPITAIS])
    parser.add_argument(
        "--somente-existentes", action="store_true",
        help="Ignora combinações ainda não baixadas (útil durante a coleta).",
    )
    parser.add_argument("--saida", type=Path, default=REPORT_FILE, help="Relatório JSON de auditoria.")
    args = parser.parse_args()
    configurar_logging()

    anos_invalidos = sorted(set(args.anos) - set(ANOS))
    ufs_validas = {c.uf for c in CAPITAIS}
    ufs_invalidas = sorted(set(args.ufs) - ufs_validas)
    if anos_invalidos or ufs_invalidas:
        parser.error(f"valores inválidos — anos: {anos_invalidos}; UFs: {ufs_invalidas}")

    capitais = [c for c in CAPITAIS if c.uf in set(args.ufs)]
    registros: list[dict[str, Any]] = []
    resumo = {"validos": 0, "invalidos": 0, "faltantes": 0, "duplicados": 0, "extras": 0}
    caminhos_esperados: set[Path] = set()
    for ano in set(args.anos):
        for capital in capitais:
            base = BASE_DIR / capital.uf / nome_base(ano, capital)
            caminhos_esperados.update(base.with_suffix(ext).resolve() for ext in (".xls", ".xlsx"))
    total = len(set(args.anos)) * len(capitais)
    atual = 0
    for ano in sorted(set(args.anos)):
        for capital in capitais:
            atual += 1
            caminho, encontrados = localizar_arquivo(ano, capital)
            if caminho is None:
                if args.somente_existentes:
                    continue
                resumo["faltantes"] += 1
                registro = {
                    "arquivo": str(Path(capital.uf) / f"{nome_base(ano, capital)}.xls"),
                    "ano_esperado": ano, "uf_esperada": capital.uf,
                    "capital_esperada": capital.nome, "status": "FALTANTE",
                    "divergencias": ["ARQUIVO: não encontrado"],
                }
                registros.append(registro)
                logging.warning("[%d/%d] FALTANTE: %s", atual, total, registro["arquivo"])
                continue
            if len(encontrados) > 1:
                resumo["duplicados"] += 1
            registro = validar_arquivo(caminho, ano, capital)
            if len(encontrados) > 1:
                registro["divergencias"].append(
                    "ARQUIVO: versões .xls e .xlsx coexistem para a mesma combinação"
                )
                registro["status"] = "INVALIDO"
            registros.append(registro)
            chave = "validos" if registro["status"] == "VALIDO" else "invalidos"
            resumo[chave] += 1
            logging.info(
                "[%d/%d] %s: %s", atual, total, registro["status"], registro["arquivo"]
            )
            for divergencia in registro["divergencias"]:
                logging.warning("    - %s", divergencia)

    # Também encontra arquivos cujo nome ou pasta não corresponde ao padrão
    # esperado. Sem esta varredura, um relatório correto salvo com o nome de
    # outra capital apareceria apenas como "faltante".
    capitais_por_uf = {capital.uf: capital for capital in CAPITAIS}
    padrao_nome = re.compile(
        r"^estado_nutricional_adulto_(\d{4})_([A-Z]{2})_(.+)$", re.IGNORECASE
    )
    for caminho in sorted(BASE_DIR.rglob("*")):
        if not caminho.is_file() or caminho.suffix.lower() not in {".xls", ".xlsx"}:
            continue
        if caminho.resolve() in caminhos_esperados:
            continue
        match = padrao_nome.fullmatch(caminho.stem)
        if not match:
            continue
        ano_nome, uf_nome, _ = match.groups()
        ano_nome_int = int(ano_nome)
        uf_nome = uf_nome.upper()
        if ano_nome_int not in set(args.anos) or uf_nome not in set(args.ufs):
            continue
        capital_nome = capitais_por_uf.get(uf_nome)
        if capital_nome is None:
            continue
        registro = validar_arquivo(caminho, ano_nome_int, capital_nome)
        if caminho.parent.name.upper() != uf_nome:
            registro["divergencias"].append(
                f"PASTA: nome informa UF {uf_nome}, arquivo está em {caminho.parent.name}"
            )
        registro["divergencias"].append(
            "ARQUIVO EXTRA: caminho não corresponde à combinação oficial esperada"
        )
        registro["status"] = "INVALIDO"
        registros.append(registro)
        resumo["extras"] += 1
        resumo["invalidos"] += 1
        logging.warning("EXTRA/INVÁLIDO: %s", registro["arquivo"])
        for divergencia in registro["divergencias"]:
            logging.warning("    - %s", divergencia)

    relatorio = {
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "diretorio_fonte": str(BASE_DIR.resolve()),
        "filtros": {"anos": sorted(set(args.anos)), "ufs": sorted(set(args.ufs))},
        "resumo": resumo,
        "registros": registros,
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(
        "Resultado: %d válidos | %d inválidos | %d faltantes | %d duplicados | %d extras",
        resumo["validos"], resumo["invalidos"], resumo["faltantes"],
        resumo["duplicados"], resumo["extras"],
    )
    logging.info("Relatório: %s", args.saida.resolve())
    return 1 if resumo["invalidos"] or resumo["faltantes"] or resumo["duplicados"] or resumo["extras"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
