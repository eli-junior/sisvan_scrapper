"""Baixa relatórios de Estado Nutricional de adultos nas capitais brasileiras.

Fluxo no SISVAN: Estado Nutricional -> 2015-2024 -> mês TODOS -> agrupar por
MUNICÍPIO -> UF -> capital -> filtros TODOS -> ADULTO -> Ver em tela -> Excel.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from sisvan_capitais import CAPITAIS, Capital, slug

URL = "https://sisaps.saude.gov.br/sisvan/relatoriopublico/index"
RAIZ = Path(__file__).parent
BASE_DIR = RAIZ / "dados" / "estado_nutricional_adultos_capitais"
ANOS_PADRAO = tuple(range(2015, 2025))
DOWNLOAD_TIMEOUT = 180
DELAY_ENTRE_REQUESTS = 3

def configurar_logging() -> None:
    log_dir = RAIZ / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"estado_nutricional_capitais_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    logging.info("Log salvo em: %s", log_file)


def criar_driver(download_dir: Path, headless: bool = False) -> webdriver.Chrome:
    download_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    if headless:
        options.add_argument("--headless=new")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    driver.implicitly_wait(3)
    driver.set_page_load_timeout(90)
    return driver


def selecionar(driver: webdriver.Chrome, nome: str, valor: str) -> None:
    elemento = driver.find_element(
        By.CSS_SELECTOR, f'#formEstadoNutricional select[name="{nome}"]'
    )
    Select(elemento).select_by_value(valor)


def limpar_downloads(download_dir: Path) -> None:
    for arquivo in download_dir.iterdir():
        if arquivo.is_file():
            arquivo.unlink()


def aguardar_download(download_dir: Path, timeout: int = DOWNLOAD_TIMEOUT) -> Path | None:
    inicio = time.monotonic()
    while time.monotonic() - inicio < timeout:
        parciais = list(download_dir.glob("*.crdownload")) + list(download_dir.glob("*.tmp"))
        completos = [
            p for p in download_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".xls", ".xlsx"}
        ]
        if completos and not parciais:
            return max(completos, key=lambda p: p.stat().st_mtime)
        time.sleep(1)
    return None


def mensagem_alerta(driver: webdriver.Chrome) -> str | None:
    try:
        alerta = driver.switch_to.alert
        texto = alerta.text
        alerta.accept()
        return texto
    except NoAlertPresentException:
        return None


def captcha_presente(driver: webdriver.Chrome) -> bool:
    seletores = (
        'iframe[src*="recaptcha"]', '.g-recaptcha',
        'textarea[name="g-recaptcha-response"]',
    )
    return any(driver.find_elements(By.CSS_SELECTOR, seletor) for seletor in seletores)


def abrir_relatorio(
    driver: webdriver.Chrome, ano: int, capital: Capital
) -> tuple[str, set[str]]:
    """Preenche o formulário e retorna o handle da aba de resultado."""
    wait = WebDriverWait(driver, 25)
    driver.get(URL)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'a.showSingle[target="1"]'))).click()
    wait.until(EC.visibility_of_element_located((By.ID, "div1")))

    selecionar(driver, "nuAno", str(ano))
    selecionar(driver, "nuMes[]", "99")
    selecionar(driver, "tpFiltro", "M")
    selecionar(driver, "coUfIbge", capital.codigo_uf)
    wait.until(
        lambda d: any(
            opcao.get_attribute("value") == capital.codigo_municipio
            for opcao in Select(d.find_element(By.ID, "coMunicipioIbge")).options
        )
    )
    selecionar(driver, "coMunicipioIbge", capital.codigo_municipio)

    selecionar(driver, "st_cobertura", "99")
    selecionar(driver, "nu_ciclo_vida", "3")  # ADULTO
    selecionar(driver, "ds_sexo2", "1")
    selecionar(driver, "ds_raca_cor2", "99")
    selecionar(driver, "co_sistema_origem", "0")
    selecionar(driver, "CO_POVO_COMUNIDADE", "TODOS")
    selecionar(driver, "CO_ESCOLARIDADE", "TODOS")

    # Primeiro abre a visualização em tela; o Excel é gerado na página seguinte.
    driver.execute_script(
        "document.querySelector('#formEstadoNutricional [name=coVisualizacao]').value='1'"
    )
    handles_antes = set(driver.window_handles)
    driver.find_element(By.ID, "verTela").click()
    captcha_avisado = False
    limite = time.monotonic() + 180
    while time.monotonic() < limite:
        novos_handles = set(driver.window_handles) - handles_antes
        if len(novos_handles) == 1:
            break
        alerta = mensagem_alerta(driver)
        if alerta:
            raise RuntimeError(f"O portal recusou o formulário: {alerta}")
        if captcha_presente(driver) and not captcha_avisado:
            logging.warning(
                "    reCAPTCHA solicitado: resolva-o manualmente na janela do Chrome."
            )
            captcha_avisado = True
        time.sleep(1)
    else:
        if captcha_avisado:
            raise RuntimeError("O reCAPTCHA não foi concluído em 180 segundos.")
        raise RuntimeError("A aba de resultado não foi aberta pelo portal.")

    handle_resultado = (set(driver.window_handles) - handles_antes).pop()
    return handle_resultado, handles_antes


def clicar_gerar_excel(driver: webdriver.Chrome, handle_resultado: str) -> None:
    driver.switch_to.window(handle_resultado)
    wait = WebDriverWait(driver, 60)
    wait.until(lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"})
    candidatos = (
        "//button[contains(translate(normalize-space(.), 'EXCEL', 'excel'), 'excel')]",
        "//a[contains(translate(normalize-space(.), 'EXCEL', 'excel'), 'excel')]",
        "//input[contains(translate(@value, 'EXCEL', 'excel'), 'excel')]",
    )
    for xpath in candidatos:
        for elemento in driver.find_elements(By.XPATH, xpath):
            if elemento.is_displayed() and elemento.is_enabled():
                driver.execute_script("arguments[0].click()", elemento)
                return
    raise RuntimeError("Botão 'Gerar Excel' não encontrado na tela de resultados.")


def baixar_relatorio(
    driver: webdriver.Chrome, download_dir: Path, destino_base: Path,
    ano: int, capital: Capital, max_tentativas: int = 3,
) -> Path | None:
    for tentativa in range(1, max_tentativas + 1):
        limpar_downloads(download_dir)
        handles_antes: set[str] = set()
        handle_resultado: str | None = None
        try:
            handle_resultado, handles_antes = abrir_relatorio(driver, ano, capital)
            clicar_gerar_excel(driver, handle_resultado)
            arquivo = aguardar_download(download_dir)
            if not arquivo:
                raise RuntimeError(f"Download não terminou em {DOWNLOAD_TIMEOUT} segundos.")
            destino = destino_base.with_suffix(arquivo.suffix.lower())
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(arquivo), destino)
            return destino
        except Exception as exc:  # mantém a coleta longa resiliente
            logging.error("    Tentativa %d/%d falhou: %s", tentativa, max_tentativas, exc)
            if "recaptcha" in str(exc).lower():
                return None
            time.sleep(2 * tentativa)
        finally:
            if handle_resultado and handle_resultado in driver.window_handles:
                driver.switch_to.window(handle_resultado)
                driver.close()
            originais = handles_antes & set(driver.window_handles)
            if originais:
                driver.switch_to.window(next(iter(originais)))
    return None


def arquivo_existente(destino_base: Path) -> Path | None:
    for extensao in (".xls", ".xlsx"):
        candidato = destino_base.with_suffix(extensao)
        if candidato.exists():
            return candidato
    return None


def validar_argumentos(anos: list[int], ufs: list[str]) -> None:
    anos_invalidos = sorted(set(anos) - set(ANOS_PADRAO))
    if anos_invalidos:
        raise SystemExit(f"Anos fora do intervalo 2015-2024: {anos_invalidos}")
    ufs_validas = {capital.uf for capital in CAPITAIS}
    ufs_invalidas = sorted(set(ufs) - ufs_validas)
    if ufs_invalidas:
        raise SystemExit(f"UFs inválidas: {', '.join(ufs_invalidas)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Baixa Estado Nutricional de adultos para as 27 capitais (2015-2024)."
    )
    parser.add_argument("--anos", nargs="+", type=int, default=list(ANOS_PADRAO))
    parser.add_argument(
        "--ufs", nargs="+", type=str.upper,
        default=[capital.uf for capital in CAPITAIS],
        help="UFs desejadas (ex.: SP RJ DF).",
    )
    parser.add_argument("--replace", action="store_true", help="Refaz os arquivos selecionados.")
    parser.add_argument("--headless", action="store_true", help="Executa o Chrome sem janela.")
    args = parser.parse_args()
    validar_argumentos(args.anos, args.ufs)
    configurar_logging()

    anos = sorted(set(args.anos))
    ufs = set(args.ufs)
    capitais = [capital for capital in CAPITAIS if capital.uf in ufs]
    download_dir = RAIZ / "dados" / "_temp_estado_nutricional_capitais"
    download_dir.mkdir(parents=True, exist_ok=True)
    if args.replace:
        for ano in anos:
            for capital in capitais:
                nome = f"estado_nutricional_adulto_{ano}_{capital.uf}_{slug(capital.nome)}"
                destino_base = BASE_DIR / capital.uf / nome
                for extensao in (".xls", ".xlsx"):
                    destino_base.with_suffix(extensao).unlink(missing_ok=True)

    total = len(anos) * len(capitais)
    logging.info("Estado Nutricional | Adultos | Capitais brasileiras")
    logging.info("Anos: %s | UFs: %s | Total esperado: %d", anos, sorted(ufs), total)
    logging.info("Saída: %s", BASE_DIR.resolve())

    driver = criar_driver(download_dir, headless=args.headless)
    sucessos = falhas = existentes = atual = 0
    try:
        for ano in anos:
            for capital in capitais:
                atual += 1
                nome = f"estado_nutricional_adulto_{ano}_{capital.uf}_{slug(capital.nome)}"
                destino_base = BASE_DIR / capital.uf / nome
                existente = arquivo_existente(destino_base)
                if existente:
                    existentes += 1
                    logging.info("[%d/%d] Já existe: %s", atual, total, existente.name)
                    continue
                logging.info("[%d/%d] %d | %s/%s...", atual, total, ano, capital.nome, capital.uf)
                salvo = baixar_relatorio(driver, download_dir, destino_base, ano, capital)
                if salvo:
                    sucessos += 1
                    logging.info("    OK: %s", salvo.name)
                else:
                    falhas += 1
                    logging.error("    FALHOU: %d | %s/%s", ano, capital.nome, capital.uf)
                time.sleep(DELAY_ENTRE_REQUESTS)
    except KeyboardInterrupt:
        logging.warning("Execução interrompida; os arquivos concluídos foram preservados.")
        return 130
    finally:
        driver.quit()
        shutil.rmtree(download_dir, ignore_errors=True)

    logging.info("Concluído: %d novos, %d existentes, %d falhas.", sucessos, existentes, falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
