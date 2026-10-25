"""Backtest do modelo com 2022: base = 1º turno, verdade = 2º turno (Lula 50,90%).

Reproduz a apuração seção por seção em vários cenários de ordem de chegada (inclusive a
ordem real das zonas em 2022) e, em cada ponto da apuração, compara a projeção com o
resultado final e com a extrapolação ingênua do parcial.

Uso: python3 backtest_2022.py [--sims 3000]
"""

import argparse
import json
import os

import numpy as np

import modelo
import tse

AQUI = os.path.dirname(__file__)
CHECKPOINTS = [0.01, 0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90]


def carregar():
    d = np.load(os.path.join(AQUI, "data", "secoes_2022.npz"))
    chave = np.char.add(d["uf"], d["mun"])
    muns, mun_idx = np.unique(chave, return_inverse=True)
    with open(os.path.join(AQUI, "data", "base_1t_2026.json")) as f:
        capitais = {m["uf"] + m["cd"] for m in json.load(f)["municipios"] if m["capital"]}

    def soma(x):
        return np.bincount(mun_idx, x, len(muns))

    t1, t2 = d["t1"].astype(float), d["t2"].astype(float)
    ufs = np.array([m[:2] for m in muns])
    base = {
        "uf": ufs,
        "regiao": np.array([tse.REGIAO[u] for u in ufs]),
        "a1": soma(t1[:, 0]),
        "b1": soma(t1[:, 1]),
        "validos1": soma(t1[:, :3].sum(1)),
        "comparec1": soma(t1.sum(1)),
        "eleitores": soma(d["aptos"]),
        "capital": np.array([m in capitais for m in muns]),
    }
    secoes = {"mun_idx": mun_idx, "t2": t2, "aptos": d["aptos"], "zona": d["zona"],
              "fim_zona": d["fim_zona_2t"], "uf": d["uf"]}
    return base, secoes


def tempos_de_chegada(cenario, base, secoes, rng):
    """Um 'horário' de chegada por seção; só a ordem importa."""
    n = len(secoes["mun_idx"])
    zona_id = np.unique(np.char.add(np.char.add(secoes["uf"], base["uf"][secoes["mun_idx"]]),
                                    secoes["zona"].astype(str)), return_inverse=True)[1]
    ruido_zona = rng.random(zona_id.max() + 1)[zona_id]
    u = rng.random(n)
    if cenario == "ordem real 2022 (zonas)":
        fim = np.where(np.isnan(secoes["fim_zona"]), np.nanmedian(secoes["fim_zona"]), secoes["fim_zona"])
        return u * np.maximum(fim, 1)
    if cenario == "Sul/Sudeste primeiro":
        atraso = {"S": 0.0, "SE": 0.15, "CO": 0.2, "N": 0.5, "NE": 0.7, "EX": 0.9}
        reg = base["regiao"][secoes["mun_idx"]]
        return np.vectorize(atraso.get)(reg) + 0.6 * ruido_zona + 0.3 * u
    if cenario == "Nordeste primeiro":
        atraso = {"NE": 0.0, "N": 0.15, "CO": 0.4, "S": 0.5, "SE": 0.6, "EX": 0.9}
        reg = base["regiao"][secoes["mun_idx"]]
        return np.vectorize(atraso.get)(reg) + 0.6 * ruido_zona + 0.3 * u
    if cenario == "pequenos primeiro":
        porte = np.log10(np.maximum(base["eleitores"][secoes["mun_idx"]], 100))
        return porte / 7 + 0.3 * ruido_zona + 0.2 * u
    if cenario == "aleatório (por zona)":
        return ruido_zona + 0.3 * u
    raise ValueError(cenario)


def apurado_ate(chegou, base, secoes):
    m, t2 = secoes["mun_idx"][chegou], secoes["t2"][chegou]
    n = len(base["a1"])
    return {
        "a2": np.bincount(m, t2[:, 0], n),
        "b2": np.bincount(m, t2[:, 1], n),
        "comparec2": np.bincount(m, t2.sum(1), n),
        "eleitores_apurados": np.bincount(m, secoes["aptos"][chegou], n),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--params", default="{}", help="JSON com parâmetros do modelo a sobrescrever")
    ap.add_argument("--cenarios", default="")
    args = ap.parse_args()
    params = json.loads(args.params)

    base, secoes = carregar()
    verdade = secoes["t2"][:, 0].sum() / secoes["t2"][:, :2].sum()
    print(f"verdade 2T 2022: Lula {verdade:.2%}  |  base 1T (só os dois): {base['a1'].sum() / (base['a1'] + base['b1']).sum():.2%}")

    cenarios = ["ordem real 2022 (zonas)", "Sul/Sudeste primeiro", "Nordeste primeiro",
                "pequenos primeiro", "aleatório (por zona)"]
    if args.cenarios:
        cenarios = [c for c in cenarios if any(s in c for s in args.cenarios.split(","))]
    rng = np.random.default_rng(42)
    linhas = []
    for cenario in cenarios:
        t = tempos_de_chegada(cenario, base, secoes, rng)
        print(f"\n== {cenario}")
        print(" apur.  ingênuo  projeção [90%]            P(Lula)  erro")
        for c in CHECKPOINTS:
            chegou = t <= np.quantile(t, c)
            r = modelo.projetar(base, apurado_ate(chegou, base, secoes), params, n_sim=args.sims)
            pr = r["projecao"]
            cobre = pr["p05"] <= verdade <= pr["p95"]
            linhas.append({"cenario": cenario, "apurado": c, "erro": pr["p50"] - verdade,
                           "ingenuo": r["apurado"]["fatia_a"] - verdade, "cobre": cobre,
                           "largura": pr["p95"] - pr["p05"], "prob": r["prob_a"]})
            print(f" {c:5.0%}  {r['apurado']['fatia_a']:7.2%}  {pr['p50']:.2%} [{pr['p05']:.2%}, {pr['p95']:.2%}]"
                  f"  {r['prob_a']:6.1%}  {pr['p50'] - verdade:+.2%} {'' if cobre else '  <- fora do intervalo'}")

    erros = np.array([l["erro"] for l in linhas])
    ingenuos = np.array([l["ingenuo"] for l in linhas])
    print(f"\nerro médio absoluto: modelo {np.abs(erros).mean():.2%}  |  ingênuo {np.abs(ingenuos).mean():.2%}")
    print(f"cobertura do intervalo de 90%: {np.mean([l['cobre'] for l in linhas]):.0%} "
          f"(largura média {np.mean([l['largura'] for l in linhas]):.2%})")
    with open(os.path.join(AQUI, "data", "backtest_2022.json"), "w") as f:
        json.dump({"verdade": verdade, "linhas": linhas}, f, ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
