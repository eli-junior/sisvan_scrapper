"""
Script para download automatizado de dados do SISVAN
- Consumo de Alimentos Ultraprocessados
- Crianças de 5 a 9 anos
- Anos 2015 a 2024
"""

import os
import sys
import time
import glob
import shutil
import argparse
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

URL = "https://sisaps.saude.gov.br/sisvan/relatoriopublico/index"
BASE_DIR = Path(__file__).parent / "dados"
DOWNLOAD_TIMEOUT = 120  # segundos para aguardar download
DELAY_ENTRE_REQUESTS = 3  # segundos entre cada request

ANOS = list(range(2015, 2025))

SEXOS = {"F": "FEMININO", "M": "MASCULINO"}

RACAS = {
    "01": "Branca",
    "02": "Preta",
    "03": "Amarela",
    "04": "Parda",
    "05": "Indigena",
}

REGIOES = {
    "5": "CENTRO-OESTE",
    "2": "NORDESTE",
    "1": "NORTE",
    "3": "SUDESTE",
    "4": "SUL",
}


def criar_driver(download_dir: Path, headless: bool = False) -> webdriver.Chrome:
    """Cria e configura o ChromeDriver com download automático."""
    download_dir.mkdir(parents=True, exist_ok=True)
    download_path = str(download_dir.resolve())

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": download_path,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(5)

    # Habilitar download em headless
    if headless:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": download_path},
        )

    return driver


def aguardar_download(download_dir: Path, timeout: int = DOWNLOAD_TIMEOUT) -> str | None:
    """Aguarda o download de um arquivo completar e retorna o caminho."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        # Procura arquivos recém-baixados (não .crdownload nem .tmp)
        arquivos = list(download_dir.glob("*"))
        arquivos_validos = [
            f for f in arquivos
            if f.is_file()
            and not f.suffix == ".crdownload"
            and not f.suffix == ".tmp"
            and not f.name.startswith(".")
        ]
        # Verifica se não há downloads em andamento
        downloads_em_andamento = list(download_dir.glob("*.crdownload"))
        if arquivos_validos and not downloads_em_andamento:
            # Retorna o arquivo mais recente
            mais_recente = max(arquivos_validos, key=lambda f: f.stat().st_mtime)
            return str(mais_recente)
        time.sleep(1)
    return None


def limpar_downloads(download_dir: Path):
    """Remove todos os arquivos da pasta de download temporária."""
    for f in download_dir.iterdir():
        if f.is_file():
            f.unlink()


def clicar_aba_consumo(driver):
    """Clica no botão 'SELECIONAR RELATÓRIO' do card Consumo Alimentar.

    O card usa a classe .showSingle com target="3" para exibir div3 (formConsumo).
    """
    wait = WebDriverWait(driver, 15)
    aba = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'a.showSingle[target="3"]')
        )
    )
    aba.click()
    # Aguarda div3 (formConsumo) ficar visível
    wait.until(EC.visibility_of_element_located((By.ID, "div3")))
    time.sleep(1)


def selecionar_campo(driver, select_id: str, valor: str, by_name: bool = False, form_id: str = "formConsumo"):
    """Seleciona um valor em um campo select via JavaScript.

    Os selects usam bootstrap-select, então não é possível usar Select() do Selenium.
    Em vez disso, setamos o valor via JS e disparamos o evento 'change'.
    """
    if by_name:
        js = f"""
            var form = document.getElementById('{form_id}');
            var sel = form.querySelector('[name="{select_id}"]');
            if (sel) {{
                sel.value = '{valor}';
                $(sel).trigger('change');
                $(sel).selectpicker('refresh');
            }}
        """
    else:
        js = f"""
            var sel = document.getElementById('{select_id}');
            if (sel) {{
                sel.value = '{valor}';
                $(sel).trigger('change');
                $(sel).selectpicker('refresh');
            }}
        """
    driver.execute_script(js)
    time.sleep(0.5)


def preencher_formulario_base(driver, ano: int, agrupar_por: str = "F", regiao: str = None):
    """
    Preenche os campos base do formulário de Consumo Alimentar (2015+).

    Args:
        driver: WebDriver
        ano: Ano de referência (2015-2024)
        agrupar_por: "F" para BRASIL, "R" para REGIÃO
        regiao: Código da região (quando agrupar_por="R")
    """
    # 1. Navegar para a página
    driver.get(URL)
    time.sleep(2)

    # 2. Clicar na aba CONSUMO ALIMENTAR
    clicar_aba_consumo(driver)

    # 3. Ano de Referência
    selecionar_campo(driver, "nuAno2", str(ano))

    # 4. Mês de Referência = TODOS
    selecionar_campo(driver, "nuMes2", "99")

    # 5. Agrupar por
    selecionar_campo(driver, "tpFiltro", agrupar_por, by_name=True)
    time.sleep(1)

    # 6. Se REGIÃO, selecionar a região
    if agrupar_por == "R" and regiao:
        selecionar_campo(driver, "coRegiao", regiao)
        time.sleep(0.5)

    # 7. Faixa Etária = 2 anos ou mais
    selecionar_campo(driver, "TP_RELATORIO5", "3")
    time.sleep(1)

    # 8. Fases da Vida = Crianças de 5 a 9 anos
    selecionar_campo(driver, "MAIOR_5_ANOS", "1")
    time.sleep(1)

    # 9. Tipo de Relatório = Consumo de Alimentos Ultraprocessados
    selecionar_campo(driver, "OPCOES_MAIOR_2_ANOS5", "10")
    time.sleep(0.5)


def clicar_salvar_excel(driver):
    """Seta coVisualizacao=2 e clica no botão Salvar em Excel da seção 2015+."""
    # Setar hidden field para download Excel
    driver.execute_script('document.getElementById("coVisualizacao").value = "2";')
    time.sleep(0.3)

    # O 2º botão "Salvar em Excel" (id=verTela) pertence à seção 2015+
    # Precisamos encontrar o botão correto dentro do form
    botoes = driver.find_elements(By.CSS_SELECTOR, "#formConsumo button[type='submit']")
    if len(botoes) >= 2:
        botoes[1].click()  # 2º botão = seção 2015+
    else:
        botoes[0].click()


def baixar_com_retry(driver, download_dir: Path, dest_path: Path, max_tentativas: int = 3):
    """Executa o download com retry."""
    for tentativa in range(1, max_tentativas + 1):
        limpar_downloads(download_dir)
        try:
            clicar_salvar_excel(driver)
            arquivo = aguardar_download(download_dir)
            if arquivo:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(arquivo, str(dest_path))
                return True
            else:
                print(f"    Timeout no download (tentativa {tentativa}/{max_tentativas})")
        except Exception as e:
            print(f"    Erro (tentativa {tentativa}/{max_tentativas}): {e}")
        time.sleep(2)
    return False


def parte01_por_sexo(driver, download_dir: Path, anos: list[int]):
    """Parte 01: Download por sexo (FEMININO e MASCULINO)."""
    dest_dir = BASE_DIR / "por_sexo"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(anos) * len(SEXOS)
    atual = 0

    print("\n=== PARTE 01: POR SEXO ===")
    for ano in anos:
        for cod_sexo, nome_sexo in SEXOS.items():
            atual += 1
            nome_arquivo = f"sexo_{ano}_{nome_sexo}.xls"
            dest_path = dest_dir / nome_arquivo

            if dest_path.exists():
                print(f"  [{atual}/{total}] {nome_arquivo} já existe, pulando.")
                continue

            print(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
            preencher_formulario_base(driver, ano, agrupar_por="F")
            selecionar_campo(driver, "ds_sexo3", cod_sexo)
            time.sleep(0.5)

            if baixar_com_retry(driver, download_dir, dest_path):
                print(f"    OK: {nome_arquivo}")
            else:
                print(f"    FALHOU: {nome_arquivo}")

            time.sleep(DELAY_ENTRE_REQUESTS)


def parte02_por_raca(driver, download_dir: Path, anos: list[int]):
    """Parte 02: Download por raça/cor."""
    dest_dir = BASE_DIR / "por_raca_cor"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(anos) * len(RACAS)
    atual = 0

    print("\n=== PARTE 02: POR RAÇA/COR ===")
    for ano in anos:
        for cod_raca, nome_raca in RACAS.items():
            atual += 1
            nome_arquivo = f"raca_{ano}_{nome_raca}.xls"
            dest_path = dest_dir / nome_arquivo

            if dest_path.exists():
                print(f"  [{atual}/{total}] {nome_arquivo} já existe, pulando.")
                continue

            print(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
            preencher_formulario_base(driver, ano, agrupar_por="F")
            selecionar_campo(driver, "ds_raca_cor3", cod_raca)
            time.sleep(0.5)

            if baixar_com_retry(driver, download_dir, dest_path):
                print(f"    OK: {nome_arquivo}")
            else:
                print(f"    FALHOU: {nome_arquivo}")

            time.sleep(DELAY_ENTRE_REQUESTS)


def parte03_por_regiao(driver, download_dir: Path, anos: list[int]):
    """Parte 03: Download por região."""
    dest_dir = BASE_DIR / "por_regiao"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(anos) * len(REGIOES)
    atual = 0

    print("\n=== PARTE 03: POR REGIÃO ===")
    for ano in anos:
        for cod_regiao, nome_regiao in REGIOES.items():
            atual += 1
            nome_arquivo = f"regiao_{ano}_{nome_regiao}.xls"
            dest_path = dest_dir / nome_arquivo

            if dest_path.exists():
                print(f"  [{atual}/{total}] {nome_arquivo} já existe, pulando.")
                continue

            print(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
            preencher_formulario_base(driver, ano, agrupar_por="R", regiao=cod_regiao)

            if baixar_com_retry(driver, download_dir, dest_path):
                print(f"    OK: {nome_arquivo}")
            else:
                print(f"    FALHOU: {nome_arquivo}")

            time.sleep(DELAY_ENTRE_REQUESTS)


def main():
    parser = argparse.ArgumentParser(description="Download de dados SISVAN")
    parser.add_argument(
        "--parte",
        type=int,
        choices=[1, 2, 3],
        help="Executar apenas uma parte (1=sexo, 2=raça/cor, 3=região). Sem este argumento, executa todas.",
    )
    parser.add_argument(
        "--ano",
        type=int,
        help="Executar apenas para um ano específico (ex: 2024). Sem este argumento, executa 2015-2024.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Executar em modo headless (sem abrir janela do navegador).",
    )
    args = parser.parse_args()

    anos = [args.ano] if args.ano else ANOS

    # Pasta temporária para downloads do Chrome
    download_dir = BASE_DIR / "_temp_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"SISVAN - Download de Dados de Consumo Alimentar")
    print(f"Anos: {anos[0]}–{anos[-1]}")
    print(f"Pasta de saída: {BASE_DIR.resolve()}")
    print(f"Headless: {'Sim' if args.headless else 'Não'}")

    driver = criar_driver(download_dir, headless=args.headless)

    try:
        partes = [args.parte] if args.parte else [1, 2, 3]

        if 1 in partes:
            parte01_por_sexo(driver, download_dir, anos)
        if 2 in partes:
            parte02_por_raca(driver, download_dir, anos)
        if 3 in partes:
            parte03_por_regiao(driver, download_dir, anos)

        print("\n=== CONCLUÍDO ===")

        # Contagem de arquivos baixados
        for subdir in ["por_sexo", "por_raca_cor", "por_regiao"]:
            pasta = BASE_DIR / subdir
            if pasta.exists():
                n = len(list(pasta.glob("*.xls")))
                print(f"  {subdir}: {n} arquivos")

    finally:
        driver.quit()
        # Limpar pasta temporária
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
