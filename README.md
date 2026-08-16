# SISVAN Scrapper

Coleta automatizada de relatórios públicos do **SISVAN** (Sistema de Vigilância Alimentar e Nutricional) via Selenium, com validação e consolidação dos dados em Excel.

**Fonte:** [sisaps.saude.gov.br/sisvan/relatoriopublico](https://sisaps.saude.gov.br/sisvan/relatoriopublico/index)

---

## Escopo dos dados

| Parâmetro | Valor |
|---|---|
| **Anos** | 2015 a 2024 |
| **Mês de referência** | Todos |
| **Fase da vida** | Crianças de 5 a 9 anos |
| **Faixa etária** | 2 anos ou mais |

### Relatórios coletados

| Script | Indicador | Filtros |
|---|---|---|
| `download_consumo_alimentar.py` | Consumo de Alimentos Ultraprocessados | Por sexo, raça/cor e região |
| `download_estado_nutricional.py` | Estado Nutricional — IMC × Idade | Por sexo, raça/cor e região |

---

## Estrutura do projeto

```
sisvan_scrapper/
│
├── download_consumo_alimentar.py    # Download do relatório de Consumo Alimentar
├── download_estado_nutricional.py   # Download do relatório de Estado Nutricional
├── valida_consumo_alimentar.py      # Validação e consolidação do Consumo Alimentar
├── requirements.txt
│
├── dados/
│   ├── consumo_alimentar/
│   │   ├── por_sexo/                # sexo_AAAA_FEMININO.xls / MASCULINO.xls
│   │   ├── por_raca_cor/            # raca_AAAA_Branca.xls / Preta / Amarela / Parda / Indigena
│   │   ├── por_regiao/              # regiao_AAAA.xls  (todas regiões por arquivo)
│   │   ├── consolidado_por_sexo.xlsx
│   │   ├── consolidado_por_raca_cor.xlsx
│   │   └── consolidado_por_regiao.xlsx
│   │
│   └── estado_nutricional/
│       ├── por_sexo/
│       ├── por_raca_cor/
│       └── por_regiao/
│
└── logs/                            # Logs de cada execução (console + arquivo)
```

---

## Instalação

```bash
git clone git@github.com:eli-junior/sisvan_scrapper.git
cd sisvan_scrapper
pip install -r requirements.txt
```

> O ChromeDriver é baixado automaticamente via `webdriver-manager`.

---

## Uso

### Download — Consumo Alimentar

```bash
# Baixa apenas os arquivos faltantes (comportamento padrão)
python download_consumo_alimentar.py

# Apaga tudo e baixa novamente do zero
python download_consumo_alimentar.py --replace
```

Gera **60 arquivos** (10 anos × 3 filtros × sexo/raça/região):

| Pasta | Arquivos gerados |
|---|---|
| `por_sexo/` | `sexo_2015_FEMININO.xls` … `sexo_2024_MASCULINO.xls` |
| `por_raca_cor/` | `raca_2015_Branca.xls` … `raca_2024_Indigena.xls` |
| `por_regiao/` | `regiao_2015.xls` … `regiao_2024.xls` |

---

### Download — Estado Nutricional

```bash
python download_estado_nutricional.py

python download_estado_nutricional.py --replace
```

> **Nota:** O endpoint `/estadonutricional` pode apresentar instabilidade intermitente no servidor. O script trata timeouts e faz retry automático.

---

### Validação e consolidação — Consumo Alimentar

```bash
# Valida e consolida todas as pastas
python valida_consumo_alimentar.py

# Valida apenas uma pasta específica
python valida_consumo_alimentar.py --sexo
python valida_consumo_alimentar.py --raca
python valida_consumo_alimentar.py --regiao
```

O script valida cada arquivo `.xls` e consolida os dados em planilhas Excel:

| Saída | Conteúdo |
|---|---|
| `consolidado_por_sexo.xlsx` | Ano × FEMININO / MASCULINO |
| `consolidado_por_raca_cor.xlsx` | Ano × Branca / Preta / Amarela / Parda / Indígena |
| `consolidado_por_regiao.xlsx` | Ano × Centro-Oeste / Nordeste / Norte / Sudeste / Sul |

**Comportamento incremental:** arquivos já validados são reutilizados. Apenas os faltantes ou com erro são reprocessados.

**Validações aplicadas por arquivo:**

- Ano confere com o nome do arquivo
- Mês de referência = TODOS
- Abrangência = BRASIL
- Tipo = Consumo de Alimentos Ultraprocessados
- Faixa = Total de Crianças de 5 a 9 anos
- Sexo / Raça/Cor conferem com o nome do arquivo

Células com divergência são destacadas em **vermelho** na planilha consolidada.

---

## Detalhes técnicos

### Formulário de Consumo Alimentar (`formConsumo`)

O formulário usa **AngularJS + bootstrap-select**. A principal complexidade está no handler do botão "Salvar em Excel" da seção 2015+, que lê o ano de `#nuAno` (campo da seção legada) para decidir qual hidden field preencher:

```javascript
// Handler do botão 2015+ (simplificado)
var ano = $('#formConsumo #nuAno').val();
if (ano >= 2015) {
    $('#formConsumo #nuAno2015').val(ano);   // hidden field lido pelo servidor
    $('#formConsumo #tpRelatorio').val(5);
}
$('#formConsumo #coVisualizacao').val(2);    // 2 = Excel
```

Por isso o script seta `#nuAno` (seção legada) **além** de `#nuAno2` (seção 2015+), e usa `JS click` no botão (que está em seção `ng-hide` e não aceita `.click()` Selenium).

Campos relevantes:

| Campo | Descrição | Valor fixo |
|---|---|---|
| `nuAno` / `nuAno2` | Ano de referência | 2015–2024 |
| `nuMes2` | Mês | `99` (TODOS) |
| `tpFiltro` | Agrupamento | `F` (Brasil) / `R` (Região) |
| `TP_RELATORIO5` | Faixa etária | `3` (2 anos ou mais) |
| `MAIOR_2_ANOS5` | Fase da vida | `2` (Crianças de 5 a 9 anos) |
| `OPCOES_MAIOR_2_ANOS5` | Tipo de relatório | `10` (Ultraprocessados) |
| `ds_sexo5` | Sexo | `T` / `F` / `M` |
| `ds_raca_cor5` | Raça/Cor | `99` / `01`–`05` |
| `coRegiao` | Região | `99` (TODOS) |

### Formulário de Estado Nutricional (`formEstadoNutricional`)

O formulário tem `target="_blank"` (resultado abre em nova aba) e um listener de **reCAPTCHA** no evento `submit`. O bypass é feito chamando `HTMLFormElement.prototype.submit.call(form)` diretamente, o que não dispara event listeners de `submit`.

Campos relevantes:

| Campo | Descrição | Valor fixo |
|---|---|---|
| `nuAno` | Ano de referência | 2015–2024 |
| `nuMes` | Mês | `99` (TODOS) |
| `tpFiltro` | Agrupamento | `F` (Brasil) / `R` (Região) |
| `nu_ciclo_vida` | Fase da vida | `1` (CRIANÇA) |
| `nu_idade_inicio` | Idade inicial | `5` (5 anos) |
| `nu_idade_fim` | Idade final | `10` (< 10 anos) |
| `nu_indice_cri` | Índice | `4` (IMC × Idade) |
| `coMunicipioIbge` | Município | `99` (obrigatório para jQuery validate) |
| `ds_sexo2` | Sexo | `1` / `F` / `M` |
| `ds_raca_cor2` | Raça/Cor | `99` / `01`–`05` |

---

## Dependências principais

| Pacote | Uso |
|---|---|
| `selenium` | Automação do navegador |
| `webdriver-manager` | Download automático do ChromeDriver |
| `openpyxl` | Geração dos consolidados `.xlsx` |
| `xlrd` | Leitura dos arquivos `.xls` baixados |

---

## Estado Nutricional de adultos por capital

O script `download_estado_nutricional_capitais.py` coleta um relatório para
cada combinação de ano e capital, seguindo **Ver em tela → Gerar Excel**.

Filtros fixos:

- Anos de 2015 a 2024 e mês TODOS
- Agrupar por MUNICÍPIO; uma UF por vez; município igual à capital
- Região de cobertura, sexo, raça/cor, acompanhamentos, povo/comunidade e
  escolaridade em TODOS/TODAS
- Fase da vida ADULTO

```bash
# Coleta completa: 27 capitais × 10 anos = 270 arquivos
python download_estado_nutricional_capitais.py

# Teste curto ou retomada seletiva
python download_estado_nutricional_capitais.py --anos 2024 --ufs SP DF

# Refaz somente as UFs selecionadas
python download_estado_nutricional_capitais.py --anos 2024 --ufs SP --replace

# Execução sem janela do Chrome
python download_estado_nutricional_capitais.py --headless
```

Os arquivos são gravados em
`dados/estado_nutricional_adultos_capitais/UF/`. Arquivos já existentes são
pulados, portanto uma execução interrompida pode ser retomada com o mesmo
comando. Se o portal exigir reCAPTCHA, o script registra a ocorrência e não
tenta contornar o desafio.

### Validação dos relatórios por capital

O `valida_estado_nutricional_capitais.py` não altera os relatórios originais e
confere, em cada arquivo:

- se o ano e a capital escritos no **nome do arquivo** são os mesmos encontrados
  no conteúdo do relatório;
- ano, mês TODOS, fase ADULTO, sexo TODOS e relatório de Estado Nutricional;
- índice e as seis classificações de IMC;
- UF, código da UF, código IBGE e nome exato da capital;
- presença de exatamente uma linha municipal;
- soma das seis quantidades igual ao total;
- soma dos percentuais igual a aproximadamente 100% (tolerância de arredondamento).

```bash
# Audita as 270 combinações e sinaliza também arquivos faltantes
python valida_estado_nutricional_capitais.py

# Durante o download, valida apenas os arquivos que já existem
python valida_estado_nutricional_capitais.py --somente-existentes

# Recorte específico
python valida_estado_nutricional_capitais.py --anos 2015 2016 --ufs SP RJ DF
```

O resultado detalhado é salvo em
`dados/estado_nutricional_adultos_capitais/relatorio_validacao.json`. O comando
retorna código `0` quando tudo que entrou no escopo é válido e código `1` se
houver arquivo inválido, faltante ou duplicado (`.xls` e `.xlsx` simultâneos).
Arquivos com nomes extras ou fora do padrão esperado também são sinalizados.

### Consolidação final em XLS

O `consolida_estado_nutricional_capitais.py` cria um único arquivo `.xls`, com
uma aba por capital no formato `Capital-UF` e dez linhas (2015–2024). Antes de
incluir os dados, o script valida se o ano e o município do nome do arquivo
correspondem ao conteúdo.

As colunas consolidadas são:

- Ano;
- quantidade de Obesidade Grau I, proveniente de `L12`;
- quantidade de Obesidade Grau II, proveniente de `N12`;
- quantidade de Obesidade Grau III, proveniente de `P12`;
- total, proveniente de `R12`.

```bash
# Consolidação completa
python consolida_estado_nutricional_capitais.py

# Consolidação parcial
python consolida_estado_nutricional_capitais.py --anos 2015 2016 --ufs SP RJ DF
```

O arquivo é salvo em
`dados/estado_nutricional_adultos_capitais/consolidado_estado_nutricional_capitais.xls`.
Anos ainda ausentes aparecem como `NÃO COLETADO`; relatórios que não passarem
pela validação aparecem como `INVÁLIDO` e não são usados como valores.
