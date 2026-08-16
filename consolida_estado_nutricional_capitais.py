"""Consolida os relatórios de adultos do SISVAN em um único arquivo XLS.

Cada planilha representa uma capital/UF e contém uma linha por ano. Os valores
são extraídos da linha municipal, nas colunas do relatório original:

    L = quantidade Obesidade Grau I
    N = quantidade Obesidade Grau II
    P = quantidade Obesidade Grau III
    R = total

Antes da extração, o arquivo passa pela validação de ano e município contra o
nome do arquivo. Relatórios inválidos não entram como se fossem dados válidos.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import xlrd
import xlwt

from sisvan_capitais import CAPITAIS, Capital, slug
from valida_estado_nutricional_capitais import (
    ANOS,
    BASE_DIR,
    inteiro_br,
    ler_html,
    localizar_arquivo,
    validar_arquivo,
)


OUTPUT_FILE = BASE_DIR / "consolidado_estado_nutricional_capitais.xls"
LOG_FILE = Path(__file__).parent / "logs" / "consolida_estado_nutricional_capitais.log"
COLUNAS_ORIGINAIS = {
    "obesidade_grau_1": 11,  # L (índice Python 11)
    "obesidade_grau_2": 13,  # N
    "obesidade_grau_3": 15,  # P
    "total": 17,              # R
}


def configurar_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
        ],
    )


def nome_base(ano: int, capital: Capital) -> str:
    return f"estado_nutricional_adulto_{ano}_{capital.uf}_{slug(capital.nome)}"


def extrair_linha_municipal(caminho: Path, capital: Capital) -> list[str]:
    _, parser = ler_html(caminho)
    linhas = [
        linha
        for linha in parser.linhas
        if len(linha) >= 18 and linha[3].strip() == capital.codigo_municipio
    ]
    if len(linhas) != 1:
        raise ValueError(
            f"esperada 1 linha do município {capital.codigo_municipio}, "
            f"encontradas {len(linhas)}"
        )
    return linhas[0]


def extrair_dados(caminho: Path, ano: int, capital: Capital) -> dict[str, Any]:
    validacao = validar_arquivo(caminho, ano, capital)
    if validacao["status"] != "VALIDO":
        raise ValueError("; ".join(validacao["divergencias"]))

    linha = extrair_linha_municipal(caminho, capital)
    return {
        "ano": ano,
        "obesidade_grau_1": inteiro_br(linha[COLUNAS_ORIGINAIS["obesidade_grau_1"]]),
        "obesidade_grau_2": inteiro_br(linha[COLUNAS_ORIGINAIS["obesidade_grau_2"]]),
        "obesidade_grau_3": inteiro_br(linha[COLUNAS_ORIGINAIS["obesidade_grau_3"]]),
        "total": inteiro_br(linha[COLUNAS_ORIGINAIS["total"]]),
    }


def estilos() -> dict[str, xlwt.XFStyle]:
    titulo = xlwt.easyxf(
        "font: name Calibri, bold on, height 280, colour white;"
        "pattern: pattern solid, fore_colour dark_green;"
        "align: horiz center, vert center;"
    )
    cabecalho = xlwt.easyxf(
        "font: name Calibri, bold on, colour white;"
        "pattern: pattern solid, fore_colour teal;"
        "align: horiz center, vert center, wrap on;"
        "borders: bottom thin, bottom_colour gray50;"
    )
    ano = xlwt.easyxf(
        "font: name Calibri; align: horiz center, vert center;",
        num_format_str="0",
    )
    numero = xlwt.easyxf(
        "font: name Calibri; align: horiz right, vert center;",
        num_format_str="#,##0",
    )
    faltante = xlwt.easyxf(
        "font: name Calibri, italic on, colour gray50;"
        "pattern: pattern solid, fore_colour light_yellow;"
        "align: horiz center, vert center;"
    )
    invalido = xlwt.easyxf(
        "font: name Calibri, bold on, colour dark_red;"
        "pattern: pattern solid, fore_colour rose;"
        "align: horiz center, vert center;"
    )
    return {
        "titulo": titulo,
        "cabecalho": cabecalho,
        "ano": ano,
        "numero": numero,
        "faltante": faltante,
        "invalido": invalido,
    }


def criar_aba(
    workbook: xlwt.Workbook,
    capital: Capital,
    anos: list[int],
    dados: dict[int, dict[str, Any]],
    problemas: dict[int, str],
    estilos_xls: dict[str, xlwt.XFStyle],
) -> None:
    nome_aba = f"{capital.nome}-{capital.uf}"
    if len(nome_aba) > 31:
        nome_aba = f"{slug(capital.nome)[:28]}-{capital.uf}"
    ws = workbook.add_sheet(nome_aba)
    ws.panes_frozen = True
    ws.horz_split_pos = 2
    ws.remove_splits = True

    ws.write_merge(
        0, 0, 0, 4,
        f"SISVAN - Estado Nutricional | Adultos | {capital.nome}/{capital.uf}",
        estilos_xls["titulo"],
    )
    ws.row(0).height = 420
    cabecalhos = [
        "Ano",
        "Qtde. Obesidade Grau I (L12)",
        "Qtde. Obesidade Grau II (N12)",
        "Qtde. Obesidade Grau III (P12)",
        "Total (R12)",
    ]
    for coluna, cabecalho in enumerate(cabecalhos):
        ws.write(1, coluna, cabecalho, estilos_xls["cabecalho"])
    ws.row(1).height = 600

    larguras = [10, 29, 30, 30, 18]
    for coluna, largura in enumerate(larguras):
        ws.col(coluna).width = largura * 256

    for indice, ano in enumerate(anos, start=2):
        ws.write(indice, 0, ano, estilos_xls["ano"])
        registro = dados.get(ano)
        if registro:
            valores = [
                registro["obesidade_grau_1"],
                registro["obesidade_grau_2"],
                registro["obesidade_grau_3"],
                registro["total"],
            ]
            for coluna, valor in enumerate(valores, start=1):
                ws.write(indice, coluna, valor, estilos_xls["numero"])
        else:
            status = "INVÁLIDO" if ano in problemas else "NÃO COLETADO"
            estilo = estilos_xls["invalido"] if ano in problemas else estilos_xls["faltante"]
            ws.write_merge(indice, indice, 1, 4, status, estilo)


def verificar_saida(
    caminho: Path, capitais: list[Capital], anos: list[int]
) -> None:
    livro = xlrd.open_workbook(caminho, formatting_info=False)
    nomes_esperados = [f"{capital.nome}-{capital.uf}"[:31] for capital in capitais]
    if livro.sheet_names() != nomes_esperados:
        raise RuntimeError(
            f"abas inesperadas: esperado {nomes_esperados}, encontrado {livro.sheet_names()}"
        )
    for nome in nomes_esperados:
        aba = livro.sheet_by_name(nome)
        if aba.nrows != len(anos) + 2 or aba.ncols != 5:
            raise RuntimeError(
                f"estrutura inválida em {nome}: {aba.nrows} linhas x {aba.ncols} colunas"
            )
        anos_encontrados = [int(aba.cell_value(linha, 0)) for linha in range(2, aba.nrows)]
        if anos_encontrados != anos:
            raise RuntimeError(
                f"anos inesperados em {nome}: {anos_encontrados}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolida Estado Nutricional de adultos em um XLS com uma aba por capital."
    )
    parser.add_argument("--anos", nargs="+", type=int, default=list(ANOS))
    parser.add_argument("--ufs", nargs="+", type=str.upper, default=[c.uf for c in CAPITAIS])
    parser.add_argument("--saida", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    configurar_logging()

    anos = sorted(set(args.anos))
    ufs = set(args.ufs)
    anos_invalidos = sorted(set(anos) - set(ANOS))
    ufs_invalidas = sorted(ufs - {capital.uf for capital in CAPITAIS})
    if anos_invalidos or ufs_invalidas:
        parser.error(f"valores inválidos - anos: {anos_invalidos}; UFs: {ufs_invalidas}")
    capitais = [capital for capital in CAPITAIS if capital.uf in ufs]

    dados_por_capital: dict[str, dict[int, dict[str, Any]]] = {}
    problemas_por_capital: dict[str, dict[int, str]] = {}
    validos = faltantes = invalidos = 0
    total = len(anos) * len(capitais)
    atual = 0
    for capital in capitais:
        dados_por_capital[capital.uf] = {}
        problemas_por_capital[capital.uf] = {}
        for ano in anos:
            atual += 1
            caminho, encontrados = localizar_arquivo(ano, capital)
            if caminho is None:
                faltantes += 1
                logging.warning("[%d/%d] NÃO COLETADO: %d %s/%s", atual, total, ano, capital.nome, capital.uf)
                continue
            if len(encontrados) > 1:
                mensagem = "existem versões .xls e .xlsx para a mesma combinação"
                problemas_por_capital[capital.uf][ano] = mensagem
                invalidos += 1
                logging.error("[%d/%d] INVÁLIDO: %s - %s", atual, total, caminho.name, mensagem)
                continue
            try:
                dados_por_capital[capital.uf][ano] = extrair_dados(caminho, ano, capital)
                validos += 1
                logging.info("[%d/%d] OK: %s", atual, total, caminho.name)
            except Exception as exc:
                problemas_por_capital[capital.uf][ano] = str(exc)
                invalidos += 1
                logging.error("[%d/%d] INVÁLIDO: %s - %s", atual, total, caminho.name, exc)

    workbook = xlwt.Workbook(encoding="utf-8")
    estilos_xls = estilos()
    for capital in capitais:
        criar_aba(
            workbook,
            capital,
            anos,
            dados_por_capital[capital.uf],
            problemas_por_capital[capital.uf],
            estilos_xls,
        )

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    temporario = args.saida.with_name(f".{args.saida.stem}.tmp.xls")
    workbook.save(str(temporario))
    verificar_saida(temporario, capitais, anos)
    temporario.replace(args.saida)

    logging.info(
        "Concluído: %d válidos | %d não coletados | %d inválidos",
        validos, faltantes, invalidos,
    )
    logging.info("Arquivo final: %s", args.saida.resolve())
    return 1 if invalidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
