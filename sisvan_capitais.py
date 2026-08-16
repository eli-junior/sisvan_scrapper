"""Cadastro compartilhado das capitais usadas na coleta do SISVAN."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Capital:
    uf: str
    codigo_uf: str
    nome: str
    codigo_municipio: str


# Códigos do formulário do SISVAN (IBGE sem dígito verificador).
CAPITAIS = (
    Capital("AC", "12", "Rio Branco", "120040"),
    Capital("AL", "27", "Maceió", "270430"),
    Capital("AP", "16", "Macapá", "160030"),
    Capital("AM", "13", "Manaus", "130260"),
    Capital("BA", "29", "Salvador", "292740"),
    Capital("CE", "23", "Fortaleza", "230440"),
    Capital("DF", "53", "Brasília", "530010"),
    Capital("ES", "32", "Vitória", "320530"),
    Capital("GO", "52", "Goiânia", "520870"),
    Capital("MA", "21", "São Luís", "211130"),
    Capital("MT", "51", "Cuiabá", "510340"),
    Capital("MS", "50", "Campo Grande", "500270"),
    Capital("MG", "31", "Belo Horizonte", "310620"),
    Capital("PA", "15", "Belém", "150140"),
    Capital("PB", "25", "João Pessoa", "250750"),
    Capital("PR", "41", "Curitiba", "410690"),
    Capital("PE", "26", "Recife", "261160"),
    Capital("PI", "22", "Teresina", "221100"),
    Capital("RJ", "33", "Rio de Janeiro", "330455"),
    Capital("RN", "24", "Natal", "240810"),
    Capital("RS", "43", "Porto Alegre", "431490"),
    Capital("RO", "11", "Porto Velho", "110020"),
    Capital("RR", "14", "Boa Vista", "140010"),
    Capital("SC", "42", "Florianópolis", "420540"),
    Capital("SP", "35", "São Paulo", "355030"),
    Capital("SE", "28", "Aracaju", "280030"),
    Capital("TO", "17", "Palmas", "172100"),
)


def slug(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "_".join(sem_acentos.upper().split())
