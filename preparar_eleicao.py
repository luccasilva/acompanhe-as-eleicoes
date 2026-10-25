"""Compacta os dados abertos de um 2º turno presidencial (votos por seção nos dois turnos) para o backtest.

Uso: python3 preparar_eleicao.py ANO votacao_secao_ANO_BR.zip detalhe_votacao_munzona_ANO.zip
Gera data/secoes_ANO.npz com uma linha por seção:
  t1: [A, B, outros, brancos, nulos] no 1º turno     t2: o mesmo no 2º turno
  t1_elim: votos dos 4 eliminados mais votados        elim: os números deles
  aptos (rateado por zona) e fim_zona_2t (minutos depois do início da divulgação)
A é sempre o candidato do PT no 2º turno; B, o adversário.
"""

import csv
import io
import os
import sys
import zipfile
from collections import defaultdict

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
FINALISTAS = {2014: ("13", "45", 26), 2018: ("13", "17", 28), 2022: ("13", "22", 30)}  # A, B, dia do 2T (outubro)
BRANCO, NULO = "95", "96"
N_ELIMINADOS = 4


def ler_csv(caminho_zip, sufixo):
    z = zipfile.ZipFile(caminho_zip)
    nome = next(n for n in z.namelist() if n.upper().endswith(sufixo.upper()))
    with z.open(nome) as bruto:
        yield from csv.DictReader(io.TextIOWrapper(bruto, encoding="latin-1"), delimiter=";")


def main(ano, zip_secoes, zip_detalhe):
    ano = int(ano)
    a, b, dia2t = FINALISTAS[ano]
    votos = {"1": defaultdict(lambda: defaultdict(int)), "2": defaultdict(lambda: defaultdict(int))}
    for i, linha in enumerate(ler_csv(zip_secoes, "_BR.csv")):
        if linha["CD_CARGO"] != "1" or linha["NR_TURNO"] not in votos:
            continue
        secao = (linha["SG_UF"].lower(), linha["CD_MUNICIPIO"].zfill(5), int(linha["NR_ZONA"]), int(linha["NR_SECAO"]))
        votos[linha["NR_TURNO"]][secao][linha["NR_VOTAVEL"]] += int(linha["QT_VOTOS"])
        if i % 3_000_000 == 0:
            print(f"  {i:,} linhas", flush=True)

    total = defaultdict(int)
    for v in votos["1"].values():
        for n, q in v.items():
            total[n] += q
    elim = [n for n, _ in sorted(total.items(), key=lambda x: -x[1]) if n not in (a, b, BRANCO, NULO)][:N_ELIMINADOS]

    aptos_zona = defaultdict(int)
    fim_zona_2t = {}
    for linha in ler_csv(zip_detalhe, "_BR.csv"):
        if linha["CD_CARGO"] != "1":
            continue
        zona = (linha["SG_UF"].lower(), linha["CD_MUNICIPIO"].zfill(5), int(linha["NR_ZONA"]))
        if linha["NR_TURNO"] == "1":
            aptos_zona[zona] += int(linha["QT_APTOS"])
        elif linha.get("HH_ULTIMA_TOTALIZACAO") and linha.get("DT_ULTIMA_TOTALIZACAO"):
            dia = int(linha["DT_ULTIMA_TOTALIZACAO"][:2])
            h, m, s = map(int, linha["HH_ULTIMA_TOTALIZACAO"].split(":"))
            minutos = (dia - dia2t) * 1440 + (h - 17) * 60 + m + s / 60
            fim_zona_2t[zona] = max(fim_zona_2t.get(zona, minutos), minutos)

    secoes = sorted(set(votos["1"]) & set(votos["2"]))

    def linha_turno(v):
        validos_outros = sum(q for n, q in v.items() if n not in (a, b, BRANCO, NULO))
        return [v.get(a, 0), v.get(b, 0), validos_outros, v.get(BRANCO, 0), v.get(NULO, 0)]

    t1 = np.array([linha_turno(votos["1"][s]) for s in secoes], dtype=np.int64)
    t2 = np.array([linha_turno(votos["2"][s]) for s in secoes], dtype=np.int64)
    t1_elim = np.array([[votos["1"][s].get(n, 0) for n in elim] for s in secoes], dtype=np.int64)
    comparec_zona = defaultdict(int)
    for s, v in zip(secoes, t1):
        comparec_zona[s[:3]] += v.sum()
    aptos = np.array([aptos_zona.get(s[:3], 0) * t1[i].sum() / max(comparec_zona[s[:3]], 1)
                      for i, s in enumerate(secoes)])

    saida = os.path.join(AQUI, "data", f"secoes_{ano}.npz")
    np.savez_compressed(
        saida,
        uf=np.array([s[0] for s in secoes]), mun=np.array([s[1] for s in secoes]),
        zona=np.array([s[2] for s in secoes]),
        t1=t1, t2=t2, t1_elim=t1_elim, elim=np.array(elim), aptos=aptos,
        fim_zona_2t=np.array([fim_zona_2t.get(s[:3], np.nan) for s in secoes]),
    )
    v2 = t2[:, :2].sum()
    print(f"{ano}: {len(secoes):,} seções | 2T: A {t2[:, 0].sum() / v2:.2%} | eliminados {elim} "
          f"({', '.join(f'{total[n] / sum(total[k] for k in total if k not in (BRANCO, NULO)):.1%}' for n in elim)}) "
          f"| zonas com horário: {np.isfinite([fim_zona_2t.get(s[:3], np.nan) for s in secoes]).mean():.0%}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
