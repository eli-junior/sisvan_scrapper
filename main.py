#!/usr/bin/env python3
"""
SISVAN — Orquestrador (main.py)

CLI interativo para selecionar combinações de relatórios do SISVAN,
disparar o scraper e validar os dados baixados.

Uso:
    python main.py
"""

import sys
import time
import shutil
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator

# ── Selenium (driver compartilhado) ──────────────────────────────────────────
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# ── Scrapers ─────────────────────────────────────────────────────────────────
import download_consumo_alimentar as _ca
import download_estado_nutricional as _en

# ── Validadores (Consumo Alimentar) ──────────────────────────────────────────
from valida_consumo_alimentar import (
    validar_por_sexo,
    validar_por_raca,
    validar_por_regiao,
    parse_xls,
)

# =============================================================================
# Constantes e mapeamentos
# =============================================================================

ROOT     = Path(__file__).parent
DADOS    = ROOT / "dados"
TEMP_DIR = DADOS / "_temp_main"

BASE_DIRS: dict[str, Path] = {
    "consumo_alimentar":  DADOS / "consumo_alimentar",
    "estado_nutricional": DADOS / "estado_nutricional",
}

ANOS_DISPONIVEIS = list(range(2015, 2025))

SEXOS: dict[str, str] = {
    "F": "FEMININO",
    "M": "MASCULINO",
}

RACAS: dict[str, str] = {
    "01": "Branca",
    "02": "Preta",
    "03": "Amarela",
    "04": "Parda",
    "05": "Indigena",
}

# Cada formulário usa um nome de campo diferente para sexo e raça
CAMPO_SEXO: dict[str, str] = {
    "consumo_alimentar":  "ds_sexo5",
    "estado_nutricional": "ds_sexo2",
}
CAMPO_RACA: dict[str, str] = {
    "consumo_alimentar":  "ds_raca_cor5",
    "estado_nutricional": "ds_raca_cor2",
}

LABELS_RELATORIO: dict[str, str] = {
    "consumo_alimentar":  "Consumo Alimentar  (Ultraprocessados · Crianças 5-9 anos)",
    "estado_nutricional": "Estado Nutricional (IMC × Idade     · Crianças 5-9 anos)",
}

# =============================================================================
# Modelo de Job
# =============================================================================

StatusJob = Literal["PENDENTE", "OK", "FALHOU", "PULADO"]


@dataclass
class Job:
    relatorio:   str   # "consumo_alimentar" | "estado_nutricional"
    ano:         int
    dimensao:    str   # "sexo" | "raca_cor" | "regiao"
    filtro_cod:  str   # "F"/"M" | "01"-"05" | "99"
    filtro_nome: str   # "FEMININO" | "Branca" | "TODOS"
    status:      StatusJob = "PENDENTE"
    tentativas:  int   = 0

    # ── propriedades derivadas ────────────────────────────────────────────────

    @property
    def pasta(self) -> str:
        return {
            "sexo":    "por_sexo",
            "raca_cor":"por_raca_cor",
            "regiao":  "por_regiao",
        }[self.dimensao]

    @property
    def filename(self) -> str:
        if self.dimensao == "sexo":
            return f"sexo_{self.ano}_{self.filtro_nome}.xls"
        if self.dimensao == "raca_cor":
            return f"raca_{self.ano}_{self.filtro_nome}.xls"
        return f"regiao_{self.ano}.xls"

    @property
    def filepath(self) -> Path:
        return BASE_DIRS[self.relatorio] / self.pasta / self.filename

    @property
    def campos_extra(self) -> dict:
        if self.dimensao == "sexo":
            return {CAMPO_SEXO[self.relatorio]: self.filtro_cod}
        if self.dimensao == "raca_cor":
            return {CAMPO_RACA[self.relatorio]: self.filtro_cod}
        return {"coRegiao": "99"}

    @property
    def agrupar_por(self) -> str:
        return "R" if self.dimensao == "regiao" else "F"

    def __str__(self) -> str:
        tag = "CA" if self.relatorio == "consumo_alimentar" else "EN"
        return f"[{tag}] {self.ano} › {self.pasta}/{self.filename}"


# =============================================================================
# Logging
# =============================================================================

log = logging.getLogger("sisvan.main")


def configurar_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    log_file = log_dir / f"main_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


# =============================================================================
# Driver compartilhado
# =============================================================================

def criar_driver(download_dir: Path) -> webdriver.Chrome:
    download_dir.mkdir(parents=True, exist_ok=True)
    opts = Options()
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("prefs", {
        "download.default_directory":  str(download_dir.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade":  True,
        "safebrowsing.enabled":        True,
    })
    svc = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.implicitly_wait(5)
    return driver


def aguardar_download(download_dir: Path, timeout: int = 120) -> Path | None:
    inicio = time.time()
    while time.time() - inicio < timeout:
        validos = [
            f for f in download_dir.glob("*")
            if f.is_file()
            and f.suffix not in (".crdownload", ".tmp")
            and not f.name.startswith(".")
        ]
        em_andamento = list(download_dir.glob("*.crdownload"))
        if validos and not em_andamento:
            return max(validos, key=lambda f: f.stat().st_mtime)
        time.sleep(1)
    return None


def limpar_downloads(download_dir: Path) -> None:
    for f in download_dir.iterdir():
        if f.is_file():
            f.unlink()


# =============================================================================
# Download por job
# =============================================================================

def baixar_job(driver: webdriver.Chrome, download_dir: Path, job: Job) -> Path | None:
    """Chama o scraper correto e retorna o caminho do arquivo baixado (ou None)."""
    limpar_downloads(download_dir)

    if job.relatorio == "consumo_alimentar":
        _ca.preencher_e_baixar(driver, job.ano, job.campos_extra, job.agrupar_por)
    else:
        _en.preencher_e_baixar(driver, job.ano, job.campos_extra, job.agrupar_por)

    return aguardar_download(download_dir)


# =============================================================================
# Validação por job
# =============================================================================

def validar_job(job: Job, filepath: Path | None = None) -> dict:
    """
    Valida o arquivo do job.

    Consumo Alimentar: usa as funções completas de valida_consumo_alimentar.py.
    Estado Nutricional: validação básica de integridade (tamanho, binário XLS).
    """
    fp = filepath or job.filepath

    if not fp.exists() or fp.stat().st_size == 0:
        return {"valido": False, "divergencias": ["Arquivo inexistente ou vazio"]}

    if job.relatorio == "consumo_alimentar":
        if job.dimensao == "sexo":
            return validar_por_sexo(fp, job.ano, job.filtro_nome)
        if job.dimensao == "raca_cor":
            return validar_por_raca(fp, job.ano, job.filtro_nome)
        return validar_por_regiao(fp, job.ano)

    # Estado Nutricional — validação básica: verifica se é XLS binário válido
    # (não é HTML de erro nem SQL debug do servidor)
    with open(fp, "rb") as f:
        cabecalho = f.read(8)

    # XLS (.xls) começa com D0 CF 11 E0 (Compound Document)
    xls_magic = b"\xd0\xcf\x11\xe0"
    # Alguns relatórios SISVAN vêm como HTML com extensão .xls
    html_inicio = cabecalho[:5].lower()

    divergencias = []
    if cabecalho[:4] == xls_magic:
        pass  # binário XLS legítimo
    elif html_inicio in (b"<html", b"<!doc"):
        # Verifica se é HTML de erro ou SQL debug
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            conteudo = f.read(500)
        if "QUERY SQL" in conteudo:
            divergencias.append("Servidor retornou SQL debug (endpoint indisponível)")
        elif "<table" in conteudo.lower():
            pass  # HTML com tabela — aceitável (alguns relatórios são HTML)
        else:
            divergencias.append("Arquivo HTML sem tabela de dados")
    else:
        divergencias.append(f"Formato desconhecido (magic bytes: {cabecalho[:4].hex()})")

    return {
        "valido":      len(divergencias) == 0,
        "divergencias": divergencias,
        "ano":          job.ano,
    }


# =============================================================================
# Execução dos jobs
# =============================================================================

MAX_TENTATIVAS = 3


def executar_jobs(jobs: list[Job]) -> None:
    """Loop principal: baixa e valida cada job, com retry."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Garante que as pastas de destino existem
    for rel in BASE_DIRS.values():
        for sub in ("por_sexo", "por_raca_cor", "por_regiao"):
            (rel / sub).mkdir(parents=True, exist_ok=True)

    log.info(f"\n{'═'*60}")
    log.info(f"  {len(jobs)} job(s) selecionado(s)")
    log.info(f"{'═'*60}\n")

    driver = criar_driver(TEMP_DIR)

    try:
        for i, job in enumerate(jobs, 1):
            prefixo = f"[{i:>2}/{len(jobs)}]"
            log.info(f"{prefixo} {job}")

            # ── Job já existe: apenas valida ──────────────────────────────────
            if job.filepath.exists():
                resultado = validar_job(job)
                if resultado["valido"]:
                    job.status = "PULADO"
                    log.info(f"  ✓ Já existe e válido — pulando download")
                    continue
                else:
                    log.info(
                        f"  ⚠ Arquivo existe mas inválido "
                        f"({', '.join(resultado['divergencias'])}) — baixando novamente"
                    )
                    job.filepath.unlink(missing_ok=True)

            # ── Download + validação com retry ────────────────────────────────
            for tentativa in range(1, MAX_TENTATIVAS + 1):
                job.tentativas = tentativa
                log.info(f"  ↓ Download (tentativa {tentativa}/{MAX_TENTATIVAS})...")

                try:
                    tmp_arquivo = baixar_job(driver, TEMP_DIR, job)
                except Exception as exc:
                    log.error(f"  ✗ Erro no scraper: {exc}")
                    tmp_arquivo = None

                if tmp_arquivo is None:
                    log.warning("  ⚠ Timeout — nenhum arquivo baixado")
                    if tentativa < MAX_TENTATIVAS:
                        time.sleep(3)
                    continue

                # Valida o arquivo temporário antes de mover
                resultado = validar_job(job, filepath=Path(tmp_arquivo))

                if resultado["valido"]:
                    job.filepath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(tmp_arquivo, str(job.filepath))
                    job.status = "OK"
                    log.info(f"  ✓ OK — salvo em {job.filepath.relative_to(ROOT)}")
                    break
                else:
                    divs = ", ".join(resultado["divergencias"])
                    log.warning(f"  ⚠ Inválido: {divs}")
                    Path(tmp_arquivo).unlink(missing_ok=True)
                    if tentativa < MAX_TENTATIVAS:
                        time.sleep(3)
            else:
                job.status = "FALHOU"
                log.error(f"  ✗ FALHOU após {MAX_TENTATIVAS} tentativas")

            time.sleep(2)  # pausa entre jobs

    except KeyboardInterrupt:
        log.info("\n⚠  Interrompido pelo usuário")
    finally:
        driver.quit()
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR, ignore_errors=True)

    _exibir_resumo(jobs)


def _exibir_resumo(jobs: list[Job]) -> None:
    """Exibe o resumo final de execução."""
    total   = len(jobs)
    ok      = sum(1 for j in jobs if j.status == "OK")
    pulados = sum(1 for j in jobs if j.status == "PULADO")
    falhas  = sum(1 for j in jobs if j.status == "FALHOU")
    pend    = sum(1 for j in jobs if j.status == "PENDENTE")

    log.info(f"\n{'═'*60}")
    log.info(f"  RESUMO FINAL")
    log.info(f"{'─'*60}")
    log.info(f"  Total de jobs : {total}")
    log.info(f"  ✓ Baixados OK : {ok}")
    log.info(f"  ⟳ Já existiam : {pulados}")
    log.info(f"  ✗ Falharam    : {falhas}")
    if pend:
        log.info(f"  ? Pendentes   : {pend}")
    log.info(f"{'═'*60}\n")

    if falhas:
        log.info("Jobs com falha:")
        for j in jobs:
            if j.status == "FALHOU":
                log.info(f"  ✗ {j}")


# =============================================================================
# CLI — coleta de parâmetros
# =============================================================================

def _confirmar_jobs(jobs: list[Job]) -> bool:
    """Exibe resumo dos jobs e pede confirmação."""
    print(f"\n{'─'*60}")
    print(f"  {len(jobs)} job(s) a executar:\n")

    agrupado: dict[str, list[Job]] = {}
    for j in jobs:
        agrupado.setdefault(j.relatorio, []).append(j)

    for rel, grupo in agrupado.items():
        label = "Consumo Alimentar" if rel == "consumo_alimentar" else "Estado Nutricional"
        print(f"  [{label}]")
        for j in grupo:
            existe = "  ✓ existe" if j.filepath.exists() else ""
            print(f"    {j.pasta:>12}  {j.ano}  {j.filtro_nome:<12}{existe}")
        print()

    print(f"{'─'*60}")
    return inquirer.confirm(
        message="Confirma a execução?",
        default=True,
    ).execute()


def coletar_parametros() -> list[Job]:
    """
    Guia o usuário pelo preenchimento do formulário via CLI.
    Retorna a lista de Jobs a executar.
    """
    print("\n" + "═" * 60)
    print("  SISVAN — Configuração do Download")
    print("═" * 60 + "\n")
    print("  Use ↑↓ para navegar, ESPAÇO para marcar, ENTER para confirmar.\n")

    # ── 1. Tipo de relatório ──────────────────────────────────────────────────
    relatorios: list[str] = inquirer.checkbox(
        message="Tipo de Relatório",
        choices=[
            Choice("consumo_alimentar",  LABELS_RELATORIO["consumo_alimentar"]),
            Choice("estado_nutricional", LABELS_RELATORIO["estado_nutricional"]),
        ],
        validate=lambda r: len(r) > 0,
        invalid_message="Selecione ao menos um relatório.",
        instruction="(espaço = marcar)",
    ).execute()

    # ── 2. Anos ───────────────────────────────────────────────────────────────
    anos_str: list[str] = inquirer.checkbox(
        message="Anos de referência",
        choices=[Choice(str(a), str(a)) for a in ANOS_DISPONIVEIS],
        default=[str(a) for a in ANOS_DISPONIVEIS],   # todos pré-selecionados
        validate=lambda r: len(r) > 0,
        invalid_message="Selecione ao menos um ano.",
        instruction="(espaço = marcar · todos pré-selecionados)",
    ).execute()
    anos = [int(a) for a in anos_str]

    # ── 3. Dimensões de análise ───────────────────────────────────────────────
    dimensoes: list[str] = inquirer.checkbox(
        message="Dimensões de análise",
        choices=[
            Choice("sexo",    "Por Sexo"),
            Choice("raca_cor","Por Raça/Cor"),
            Choice("regiao",  "Por Região"),
        ],
        validate=lambda r: len(r) > 0,
        invalid_message="Selecione ao menos uma dimensão.",
        instruction="(espaço = marcar)",
    ).execute()

    # ── 4. Sexos (se selecionado) ─────────────────────────────────────────────
    sexos_selecionados: dict[str, str] = {}
    if "sexo" in dimensoes:
        codigos: list[str] = inquirer.checkbox(
            message="Sexo",
            choices=[
                Choice("F", "Feminino"),
                Choice("M", "Masculino"),
            ],
            default=["F", "M"],
            validate=lambda r: len(r) > 0,
            invalid_message="Selecione ao menos um sexo.",
        ).execute()
        sexos_selecionados = {cod: SEXOS[cod] for cod in codigos}

    # ── 5. Raças/Cores (se selecionado) ──────────────────────────────────────
    racas_selecionadas: dict[str, str] = {}
    if "raca_cor" in dimensoes:
        codigos_r: list[str] = inquirer.checkbox(
            message="Raça/Cor",
            choices=[
                Choice("01", "1 — Branca"),
                Choice("02", "2 — Preta"),
                Choice("03", "3 — Amarela"),
                Choice("04", "4 — Parda"),
                Choice("05", "5 — Indígena"),
            ],
            default=list(RACAS.keys()),
            validate=lambda r: len(r) > 0,
            invalid_message="Selecione ao menos uma raça/cor.",
        ).execute()
        racas_selecionadas = {cod: RACAS[cod] for cod in codigos_r}

    # ── Monta jobs ────────────────────────────────────────────────────────────
    jobs: list[Job] = []

    for rel in relatorios:
        for ano in anos:
            if "sexo" in dimensoes:
                for cod, nome in sexos_selecionados.items():
                    jobs.append(Job(rel, ano, "sexo", cod, nome))

            if "raca_cor" in dimensoes:
                for cod, nome in racas_selecionadas.items():
                    jobs.append(Job(rel, ano, "raca_cor", cod, nome))

            if "regiao" in dimensoes:
                jobs.append(Job(rel, ano, "regiao", "99", "TODOS"))

    return jobs


# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    configurar_logging()

    try:
        jobs = coletar_parametros()
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(0)

    if not jobs:
        print("Nenhum job gerado. Encerrando.")
        sys.exit(0)

    if not _confirmar_jobs(jobs):
        print("Cancelado pelo usuário.")
        sys.exit(0)

    executar_jobs(jobs)


if __name__ == "__main__":
    main()
