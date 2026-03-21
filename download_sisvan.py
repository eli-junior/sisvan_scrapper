"""
Script para download automatizado de dados do SISVAN
- Consumo de Alimentos Ultraprocessados
- Crianças de 5 a 9 anos
- Anos 2015 a 2024
"""

import time
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

URL = "https://sisaps.saude.gov.br/sisvan/relatoriopublico/index"
BASE_DIR = Path(__file__).parent / "dados"
DOWNLOAD_TIMEOUT = 120
DELAY_ENTRE_REQUESTS = 3

ANOS = list(range(2015, 2025))

SEXOS = {"F": "FEMININO", "M": "MASCULINO"}

RACAS = {
    "01": "Branca",
    "02": "Preta",
    "03": "Amarela",
    "04": "Parda",
    "05": "Indigena",
}


def configurar_logging():
    """Configura logging para console e arquivo."""
    log_dir = BASE_DIR.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"sisvan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.info(f"Log salvo em: {log_file}")
    return log_file


def criar_driver(download_dir: Path) -> webdriver.Chrome:
    """Cria e configura o ChromeDriver com download automático."""
    download_dir.mkdir(parents=True, exist_ok=True)
    download_path = str(download_dir.resolve())

    options = Options()
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
    return driver


def aguardar_download(download_dir: Path, timeout: int = DOWNLOAD_TIMEOUT) -> str | None:
    """Aguarda o download de um arquivo completar e retorna o caminho."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        arquivos = list(download_dir.glob("*"))
        arquivos_validos = [
            f for f in arquivos
            if f.is_file()
            and f.suffix != ".crdownload"
            and f.suffix != ".tmp"
            and not f.name.startswith(".")
        ]
        downloads_em_andamento = list(download_dir.glob("*.crdownload"))
        if arquivos_validos and not downloads_em_andamento:
            mais_recente = max(arquivos_validos, key=lambda f: f.stat().st_mtime)
            return str(mais_recente)
        time.sleep(1)
    return None


def limpar_downloads(download_dir: Path):
    """Remove todos os arquivos da pasta de download temporária."""
    for f in download_dir.iterdir():
        if f.is_file():
            f.unlink()


def setar_campos(driver, campos: dict):
    """Seta valores nos selects nativos do formConsumo via JS.

    O form usa bootstrap-select + AngularJS, mas o POST envia os valores
    dos <select> nativos. Setamos direto neles.

    Args:
        campos: dict de {select_id: valor} ou {name:select_name: valor}
    """
    for campo, valor in campos.items():
        if campo.startswith("name:"):
            name = campo[5:]
            driver.execute_script(f"""
                var form = document.getElementById('formConsumo');
                var sel = form.querySelector('select[name="{name}"]');
                if (sel) sel.value = '{valor}';
            """)
        else:
            driver.execute_script(f"""
                var sel = document.getElementById('{campo}');
                if (sel) sel.value = '{valor}';
            """)


def preencher_e_baixar(driver, ano: int, campos_extra: dict = None, agrupar_por: str = "F"):
    """
    Navega para a página, preenche o formulário de Consumo Alimentar (2015+)
    e clica em Salvar em Excel.

    O botão "Salvar em Excel" da seção 2015+ executa um handler jQuery que:
    1. Lê o ano de #nuAno (select da seção antiga)
    2. Se ano >= 2015: seta nuAno2015=ano, tpRelatorio=5
    3. Seta coVisualizacao=2 (Excel)
    4. Submete o form

    Por isso precisamos setar #nuAno com o ano antes de clicar.

    Args:
        driver: WebDriver
        ano: Ano de referência (2015-2024)
        campos_extra: dict de campos adicionais (ex: {"ds_sexo5": "F"})
        agrupar_por: "F" para BRASIL, "R" para REGIÃO
    """
    wait = WebDriverWait(driver, 15)

    # 1. Navegar e abrir seção Consumo Alimentar
    driver.get(URL)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'a.showSingle[target="3"]')))
    time.sleep(1)
    driver.find_element(By.CSS_SELECTOR, 'a.showSingle[target="3"]').click()
    wait.until(EC.visibility_of_element_located((By.ID, "div3")))
    time.sleep(1)

    # 2. Setar campos base nos selects nativos
    campos = {
        # Campos compartilhados (o handler do botão lê #nuAno)
        "nuAno": str(ano),           # Select pré-2015 (id=nuAno) - OBRIGATÓRIO para o handler
        "nuAno2": str(ano),          # Select 2015+ (id=nuAno2)
        "nuMes2": "99",              # Mês = TODOS
        "name:tpFiltro": agrupar_por, # F=BRASIL, R=REGIÃO

        # Campos da seção 2015+
        "TP_RELATORIO5": "3",        # Faixa Etária = 2 anos ou mais
        "MAIOR_2_ANOS5": "2",        # Fases da Vida = Crianças de 5 a 9 anos
        "OPCOES_MAIOR_2_ANOS5": "10", # Tipo = Consumo de Alimentos Ultraprocessados

        # Defaults seção 2015+
        "ds_sexo5": "T",             # Sexo = TODOS (default)
        "ds_raca_cor5": "99",        # Raça/Cor = TODAS (default)
    }

    # Aplica campos extras (sexo, raça, região)
    if campos_extra:
        campos.update(campos_extra)

    setar_campos(driver, campos)
    time.sleep(0.5)

    # 3. Clicar no 2º botão "Salvar em Excel" (seção 2015+) via JS
    # O handler jQuery vai: ler #nuAno → setar nuAno2015 → setar tpRelatorio=5 → setar coVisualizacao=2 → submit
    # Usa JS click porque o botão pode estar em seção ng-hide (Angular não atualizou)
    driver.execute_script("""
        var btns = document.querySelectorAll('#formConsumo button[type="submit"]');
        var btn = btns.length >= 2 ? btns[1] : btns[0];
        btn.click();
    """)


def baixar_com_retry(driver, download_dir: Path, dest_path: Path,
                     ano: int, campos_extra: dict = None, agrupar_por: str = "F",
                     max_tentativas: int = 3):
    """Preenche o form e faz download com retry.

    Timeout encerra a execução imediatamente (sem retry).
    Retry só ocorre em erros inesperados de exceção.
    """
    for tentativa in range(1, max_tentativas + 1):
        limpar_downloads(download_dir)
        try:
            preencher_e_baixar(driver, ano, campos_extra, agrupar_por)
            arquivo = aguardar_download(download_dir)
            if arquivo:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(arquivo, str(dest_path))
                return True
            else:
                logging.error(f"Timeout no download — encerrando sem retry.")
                return False
        except Exception as e:
            logging.error(f"Erro (tentativa {tentativa}/{max_tentativas}): {e}")
        time.sleep(2)
    return False


def parte01_por_sexo(driver, download_dir: Path):
    """Parte 01: Download por sexo (FEMININO e MASCULINO)."""
    dest_dir = BASE_DIR / "por_sexo"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(ANOS) * len(SEXOS)
    atual = 0

    logging.info("\n=== PARTE 01: POR SEXO ===")
    for ano in ANOS:
        for cod_sexo, nome_sexo in SEXOS.items():
            atual += 1
            nome_arquivo = f"sexo_{ano}_{nome_sexo}.xls"
            dest_path = dest_dir / nome_arquivo

            if dest_path.exists():
                logging.info(f"  [{atual}/{total}] {nome_arquivo} ja existe, pulando.")
                continue

            logging.info(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
            ok = baixar_com_retry(
                driver, download_dir, dest_path,
                ano=ano,
                campos_extra={"ds_sexo5": cod_sexo},
            )
            logging.info(f"    {'OK' if ok else 'FALHOU'}: {nome_arquivo}")
            time.sleep(DELAY_ENTRE_REQUESTS)


def parte02_por_raca(driver, download_dir: Path):
    """Parte 02: Download por raca/cor."""
    dest_dir = BASE_DIR / "por_raca_cor"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(ANOS) * len(RACAS)
    atual = 0

    logging.info("\n=== PARTE 02: POR RACA/COR ===")
    for ano in ANOS:
        for cod_raca, nome_raca in RACAS.items():
            atual += 1
            nome_arquivo = f"raca_{ano}_{nome_raca}.xls"
            dest_path = dest_dir / nome_arquivo

            if dest_path.exists():
                logging.info(f"  [{atual}/{total}] {nome_arquivo} ja existe, pulando.")
                continue

            logging.info(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
            ok = baixar_com_retry(
                driver, download_dir, dest_path,
                ano=ano,
                campos_extra={"ds_raca_cor5": cod_raca},
            )
            logging.info(f"    {'OK' if ok else 'FALHOU'}: {nome_arquivo}")
            time.sleep(DELAY_ENTRE_REQUESTS)


def parte03_por_regiao(driver, download_dir: Path):
    """Parte 03: Download por regiao (agrupado por REGIAO, TODOS)."""
    dest_dir = BASE_DIR / "por_regiao"
    dest_dir.mkdir(parents=True, exist_ok=True)
    total = len(ANOS)
    atual = 0

    logging.info("\n=== PARTE 03: POR REGIAO ===")
    for ano in ANOS:
        atual += 1
        nome_arquivo = f"regiao_{ano}.xls"
        dest_path = dest_dir / nome_arquivo

        if dest_path.exists():
            logging.info(f"  [{atual}/{total}] {nome_arquivo} ja existe, pulando.")
            continue

        logging.info(f"  [{atual}/{total}] Baixando {nome_arquivo}...")
        ok = baixar_com_retry(
            driver, download_dir, dest_path,
            ano=ano,
            campos_extra={"coRegiao": "99"},  # TODOS
            agrupar_por="R",
        )
        logging.info(f"    {'OK' if ok else 'FALHOU'}: {nome_arquivo}")
        time.sleep(DELAY_ENTRE_REQUESTS)


def main():
    parser = argparse.ArgumentParser(description="Download de dados SISVAN")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Apaga todos os arquivos baixados e refaz do zero.",
    )
    args = parser.parse_args()

    download_dir = BASE_DIR / "_temp_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    if args.replace:
        logging.info("--replace: Apagando dados anteriores...")
        for subdir in ["por_sexo", "por_raca_cor", "por_regiao"]:
            pasta = BASE_DIR / subdir
            if pasta.exists():
                shutil.rmtree(pasta)
            pasta.mkdir(parents=True, exist_ok=True)

    configurar_logging()
    logging.info("SISVAN - Download de Dados de Consumo Alimentar")
    logging.info(f"Anos: {ANOS[0]}-{ANOS[-1]}")
    logging.info(f"Pasta de saida: {BASE_DIR.resolve()}")

    driver = criar_driver(download_dir)

    try:
        parte01_por_sexo(driver, download_dir)
        parte02_por_raca(driver, download_dir)
        parte03_por_regiao(driver, download_dir)

        logging.info("\n=== CONCLUIDO ===")
        for subdir in ["por_sexo", "por_raca_cor", "por_regiao"]:
            pasta = BASE_DIR / subdir
            if pasta.exists():
                n = len(list(pasta.glob("*.xls")))
                logging.info(f"  {subdir}: {n} arquivos")

    except KeyboardInterrupt:
        logging.info("\n=== INTERROMPIDO PELO USUARIO ===")
    finally:
        driver.quit()
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
