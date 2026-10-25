"""Baixa o resultado de presidente do 1º turno de 2026 em cada município e salva a base.

Uso: python3 baixar_base.py   (gera data/base_1t_2026.json, ~5.760 requisições)
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import tse

SAIDA = os.path.join(os.path.dirname(__file__), "data", "base_1t_2026.json")


def main():
    cliente = tse.Cliente(max_por_segundo=40)
    config, _ = cliente.get(tse.url_municipios(tse.ELEICAO_1T))
    alvos = [(uf["cd"], mu) for uf in config["abr"] for mu in uf["mu"]]
    print(f"{len(alvos)} municípios")

    def baixar(alvo):
        uf, mu = alvo
        r = tse.resumo(cliente.get(tse.url_abrangencia(tse.ELEICAO_1T, uf, mu["cd"]))[0])
        return {
            "uf": uf,
            "cd": mu["cd"],
            "ibge": mu.get("cdi", ""),
            "nome": mu["nm"],
            "capital": mu.get("c") == "s",
            "regiao": tse.REGIAO[uf],
            "lula": r["cand"].get(tse.LULA, 0),
            "flavio": r["cand"].get(tse.FLAVIO, 0),
            "outros": {n: v for n, v in r["cand"].items() if n not in (tse.LULA, tse.FLAVIO)},
            "validos": r["validos"],
            "brancos": r["brancos"],
            "nulos": r["nulos"],
            "comparec": r["comparec"],
            "eleitores": r["eleitores"],
            "secoes": r["secoes"],
        }

    with ThreadPoolExecutor(max_workers=24) as pool:
        base = []
        for i, linha in enumerate(pool.map(baixar, alvos), 1):
            base.append(linha)
            if i % 500 == 0:
                print(f"  {i}/{len(alvos)}")

    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w") as f:
        json.dump({"eleicao": tse.ELEICAO_1T, "municipios": base}, f, ensure_ascii=False)

    lula = sum(m["lula"] for m in base)
    flavio = sum(m["flavio"] for m in base)
    validos = sum(m["validos"] for m in base)
    print(f"Lula {lula:,} ({lula / validos:.2%})  Flávio {flavio:,} ({flavio / validos:.2%})  válidos {validos:,}")


if __name__ == "__main__":
    main()
