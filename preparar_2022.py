"""Compacta os dados abertos de 2022 (presidente, por seção, 1º e 2º turno) para o backtest.

Uso: python3 preparar_2022.py votacao_secao_2022_BR.zip detalhe_votacao_munzona_2022.zip
Gera data/secoes_2022.npz com uma linha por seção e os votos dos dois turnos.
"""

import csv
import io
import os
import sys
import zipfile
from collections import defaultdict

import numpy as np

SAIDA = os.path.join(os.path.dirname(__file__), "data", "secoes_2022.npz")
LULA, BOLSONARO, BRANCO, NULO = "13", "22", "95", "96"


def ler_csv(caminho_zip, sufixo):
    z = zipfile.ZipFile(caminho_zip)
    nome = next(n for n in z.namelist() if n.endswith(sufixo))
    with z.open(nome) as bruto:
        yield from csv.DictReader(io.TextIOWrapper(bruto, encoding="latin-1"), delimiter=";")


def main(zip_secoes, zip_detalhe):
    # votos[(turno, uf, mun, zona, secao)] = [lula, bolsonaro, outros, brancos, nulos]
    votos = defaultdict(lambda: [0, 0, 0, 0, 0])
    for i, linha in enumerate(ler_csv(zip_secoes, "_BR.csv")):
        if linha["CD_CARGO"] != "1":
            continue
        chave = (linha["NR_TURNO"], linha["SG_UF"].lower(), linha["CD_MUNICIPIO"].zfill(5),
                 int(linha["NR_ZONA"]), int(linha["NR_SECAO"]))
        n, qt = linha["NR_VOTAVEL"], int(linha["QT_VOTOS"])
        col = {LULA: 0, BOLSONARO: 1, BRANCO: 3, NULO: 4}.get(n, 2)
        votos[chave][col] += qt
        if i % 2_000_000 == 0:
            print(f"  {i:,} linhas", flush=True)

    # Eleitores aptos só existem por zona: rateamos pela fração de comparecimento da seção no 1º turno.
    # O mesmo arquivo traz a hora em que cada zona terminou de ser totalizada no 2º turno, que
    # usamos como a ordem real de chegada em 2022 (minutos depois das 17h de 30/10/2022).
    aptos_zona = defaultdict(int)
    fim_zona_2t = {}
    for linha in ler_csv(zip_detalhe, "_BR.csv"):
        if linha["CD_CARGO"] != "1":
            continue
        zona = (linha["SG_UF"].lower(), linha["CD_MUNICIPIO"].zfill(5), int(linha["NR_ZONA"]))
        if linha["NR_TURNO"] == "1":
            aptos_zona[zona] += int(linha["QT_APTOS"])
        elif linha["HH_ULTIMA_TOTALIZACAO"]:
            dia = int(linha["DT_ULTIMA_TOTALIZACAO"][:2])
            h, m, s = map(int, linha["HH_ULTIMA_TOTALIZACAO"].split(":"))
            minutos = (dia - 30) * 1440 + (h - 17) * 60 + m + s / 60
            fim_zona_2t[zona] = max(fim_zona_2t.get(zona, minutos), minutos)

    secoes = sorted({k[1:] for k in votos if k[0] == "1"} & {k[1:] for k in votos if k[0] == "2"})
    t1 = np.array([votos[("1",) + s] for s in secoes], dtype=np.int64)
    t2 = np.array([votos[("2",) + s] for s in secoes], dtype=np.int64)
    comparec_zona = defaultdict(int)
    for s, v in zip(secoes, t1):
        comparec_zona[s[:3]] += v.sum()
    aptos = np.array([aptos_zona.get(s[:3], 0) * t1[i].sum() / max(comparec_zona[s[:3]], 1)
                      for i, s in enumerate(secoes)])

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    np.savez_compressed(
        SAIDA,
        uf=np.array([s[0] for s in secoes]),
        mun=np.array([s[1] for s in secoes]),
        zona=np.array([s[2] for s in secoes]),
        t1=t1, t2=t2, aptos=aptos,
        fim_zona_2t=np.array([fim_zona_2t.get(s[:3], np.nan) for s in secoes]),
    )
    tv2 = t2[:, :2].sum()
    print(f"{len(secoes):,} seções | 2T: Lula {t2[:, 0].sum() / tv2:.2%}  Bolsonaro {t2[:, 1].sum() / tv2:.2%}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
