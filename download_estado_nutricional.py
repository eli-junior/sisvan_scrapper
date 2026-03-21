"""
Script para download automatizado de dados do SISVAN
- Estado Nutricional (IMC x Idade)
- Crianças de 5 a 9 anos
- Anos 2015 a 2024

Formulário: formEstadoNutricional (target=_blank - abre em nova aba)
Bypass do reCAPTCHA via HTMLFormElement.prototype.submit.call() (nativo,
não dispara event listeners de submit, incluindo o listener de reCAPTCHA).
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
BASE_DIR = Path(__file__).parent / "dados" / "estado_nutricional"
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
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"estado_nutricional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

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


def preencher_e_baixar(
    driver: webdriver.Chrome,
    ano: int,
    campos_extra: dict | None = None,
    agrupar_por: str = "F",
) -> bool:
    """
    Navega para a página, preenche o formulário de Estado Nutricional
    e faz o download do Excel.

    Fluxo do formulário:
    - formEstadoNutricional tem target='_blank' → resultados abrem em nova aba
    - O botão de submit (#verTela) dispara evento de submit, que verifica reCAPTCHA
    - Bypass: chamamos HTMLFormElement.prototype.submit.call(form) diretamente,
      o que NÃO dispara event listeners de submit (incluindo reCAPTCHA)
    - coVisualizacao=2 para download direto de Excel

    Campos fixos:
    - nu_ciclo_vida=1 (CRIANÇA)
    - nu_idade_inicio=5 (5 anos)
    - nu_idade_fim=10 (< 10 anos)
    - nu_indice_cri=4 (IMC X Idade)
    - coMunicipioIbge=99 (obrigatório para validação jQuery)

    Args:
        driver: WebDriver
        ano: Ano de referência (2015-2024)
        campos_extra: dict de campos adicionais (ex: {"ds_sexo2": "F"})
        agrupar_por: "F" para BRASIL, "R" para REGIÃO
    """
    wait = WebDriverWait(driver, 15)
    original_handles = set(driver.window_handles)

    # 1. Navegar e abrir seção Estado Nutricional (target="1")
    driver.get(URL)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'a.showSingle[target="1"]')))
    time.sleep(1)
    driver.find_element(By.CSS_SELECTOR, 'a.showSingle[target="1"]').click()
    wait.until(EC.visibility_of_element_located((By.ID, "div1")))
    time.sleep(1)

    # 2. Setar campos base nos selects nativos
    campos = {
        "nuAno": str(ano),
        "nuMes": "99",             # Mês = TODOS
        "tpFiltro": agrupar_por,   # F=BRASIL, R=REGIÃO
        "coRegiao": "99",          # Região (TODOS, relevante só quando agrupar_por=R)
        "coMunicipioIbge": "99",   # Obrigatório para jQuery validate

        # Fases da vida / Índice - fixos
        "nu_ciclo_vida": "1",      # CRIANÇA
        "nu_idade_inicio": "5",    # 5 anos
        "nu_idade_fim": "10",      # < 10 anos
        "nu_indice_cri": "4",      # IMC X Idade

        # Defaults
        "ds_sexo2": "1",           # TODOS (pode ser sobrescrito por campos_extra)
        "ds_raca_cor2": "99",      # TODAS

        # coVisualizacao=2 para download Excel
        # (o handler #Download que define isso não existe no DOM,
        #  mas o servidor interpreta coVisualizacao=2 como Excel)
        "coVisualizacao": "2",
    }

    if campos_extra:
        campos.update(campos_extra)

    # Setar nu_idade_inicio com dispatch de change (popula nu_idade_fim via JS)
    driver.execute_script(f"""
        document.getElementById('nu_idade_inicio').value = '{campos["nu_idade_inicio"]}';
        document.getElementById('nu_idade_inicio').dispatchEvent(
            new Event('change', {{bubbles: true}})
        );
    """)
    time.sleep(0.6)

    # Setar todos os demais campos
    for campo, valor in campos.items():
        if campo == "nu_idade_inicio":
            continue  # já setado acima com dispatch
        driver.execute_script(f"""
            var el = document.getElementById('{campo}')
                   || document.querySelector('#formEstadoNutricional [name="{campo}"]');
            if (el) el.value = '{valor}';
        """)

    time.sleep(0.3)

    # 3. Submeter o form direto via HTMLFormElement.prototype.submit.call()
    #    - Bypassa o listener de reCAPTCHA (que bloqueia quando token está vazio)
    #    - Bypassa jQuery validate (campo fase não existe, causaria erro de validação)
    #    - target=_blank → abre nova aba com o resultado/download
    driver.execute_script("""
        HTMLFormElement.prototype.submit.call(
            document.getElementById('formEstadoNutricional')
        );
    """)

    # 4. Aguardar nova aba abrir
    timeout_aba = 20
    inicio = time.time()
    while time.time() - inicio < timeout_aba:
        novas = set(driver.window_handles) - original_handles
        if novas:
            break
        time.sleep(0.5)
    else:
        logging.warning("Timeout: nova aba não abriu após submit")
        return False

    # 5. Verificar se houve download (arquivo XLS/Excel) OU página de resultados
    #    - Se coVisualizacao=2 funcionar: browser dispara download diretamente
    #    - Se retornar HTML: mudar para nova aba, procurar botão Excel e clicar
    nova_aba = list(set(driver.window_handles) - original_handles)[0]
    time.sleep(2)

    # Se o conteúdo foi uma página (não download direto), verificar a nova aba
    driver.switch_to.window(nova_aba)
    time.sleep(1)
    url_aba = driver.current_url
    logging.info(f"    Nova aba URL: {url_aba}")

    # Verifica se tem botão de Excel na página de resultados
    btns_excel = driver.execute_script("""
        return Array.from(document.querySelectorAll('button, a, input[type=button]'))
            .filter(function(b) {
                var t = b.textContent.trim().toLowerCase();
                return t.indexOf('excel') >= 0 || t.indexOf('salvar') >= 0;
            })
            .map(function(b) { return {text: b.textContent.trim(), tag: b.tagName, id: b.id}; });
    """)

    if btns_excel:
        logging.info(f"    Botão Excel encontrado na página de resultados: {btns_excel}")
        # Clicar no primeiro botão Excel encontrado
        driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button, a, input[type=button]'))
                .filter(function(b) {
                    var t = b.textContent.trim().toLowerCase();
                    return t.indexOf('excel') >= 0 || t.indexOf('salvar') >= 0;
                });
            if (btns.length > 0) btns[0].click();
        """)
        time.sleep(2)

    # 6. Fechar a aba de resultados e voltar para a original
    driver.close()
    driver.switch_to.window(list(original_handles)[0])

    return True  # download será verificado por aguardar_download()


def baixar_com_retry(
    driver: webdriver.Chrome,
    download_dir: Path,
    dest_path: Path,
    ano: int,
    campos_extra: dict | None = None,
    agrupar_por: str = "F",
    max_tentativas: int = 3,
) -> bool:
    """Preenche o form e faz download com retry em caso de erro."""
    for tentativa in range(1, max_tentativas + 1):
        limpar_downloads(download_dir)
        try:
            ok = preencher_e_baixar(driver, ano, campos_extra, agrupar_por)
            if not ok:
                logging.warning(f"    Falha ao submeter form (tentativa {tentativa}/{max_tentativas})")
                time.sleep(2)
                continue

            arquivo = aguardar_download(download_dir)
            if arquivo:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(arquivo, str(dest_path))
                return True
            else:
                logging.error("    Timeout no download — encerrando sem retry.")
                return False
        except Exception as e:
            logging.error(f"    Erro (tentativa {tentativa}/{max_tentativas}): {e}")
        time.sleep(2)
    return False


def parte01_por_sexo(driver: webdriver.Chrome, download_dir: Path):
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
                campos_extra={"ds_sexo2": cod_sexo},
            )
            logging.info(f"    {'OK' if ok else 'FALHOU'}: {nome_arquivo}")
            time.sleep(DELAY_ENTRE_REQUESTS)


def parte02_por_raca(driver: webdriver.Chrome, download_dir: Path):
    """Parte 02: Download por raça/cor."""
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
                campos_extra={"ds_raca_cor2": cod_raca},
            )
            logging.info(f"    {'OK' if ok else 'FALHOU'}: {nome_arquivo}")
            time.sleep(DELAY_ENTRE_REQUESTS)


def parte03_por_regiao(driver: webdriver.Chrome, download_dir: Path):
    """Parte 03: Download por região (agrupado por REGIÃO, todas regiões juntas)."""
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
    parser = argparse.ArgumentParser(
        description="Download de dados SISVAN - Estado Nutricional (IMC X Idade, Crianças 5-9 anos)"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Apaga todos os arquivos baixados e refaz do zero.",
    )
    args = parser.parse_args()

    configurar_logging()

    download_dir = Path(__file__).parent / "dados" / "_temp_estado_nutricional"
    download_dir.mkdir(parents=True, exist_ok=True)

    if args.replace:
        logging.info("--replace: Apagando dados anteriores...")
        for subdir in ["por_sexo", "por_raca_cor", "por_regiao"]:
            pasta = BASE_DIR / subdir
            if pasta.exists():
                shutil.rmtree(pasta)
            pasta.mkdir(parents=True, exist_ok=True)

    logging.info("SISVAN - Download de Dados de Estado Nutricional")
    logging.info(f"Indicador: IMC X Idade | Fase: Criancas de 5 a 9 anos")
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
