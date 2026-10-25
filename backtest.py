"""Backtest da projeção em 2º turnos presidenciais passados, com várias ordens de chegada.

Para cada eleição (2014, 2018, 2022: base = 1º turno, verdade = 2º turno), reproduz a apuração
seção por seção em várias ordens e, em cada ponto da contagem, compara a projeção com o
resultado final. Compara o modelo em uso (modelo.py) com a versão 1 (modelo_v1.py).

Uso:
  python3 backtest.py                                  # v1 × v2, todas as eleições
  python3 backtest.py --anos 2014,2018 --so-v2 --params '{"sigma_uf": 0.01}'
  python3 backtest.py --calibrar                       # grade de parâmetros em 2014 e 2018
"""

import argparse
import itertools
import json
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import modelo
import modelo_v1
import tse

AQUI = os.path.dirname(os.path.abspath(__file__))
CHECKPOINTS = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90]
CENARIOS = ["fim da zona (zona inteira)", "fim da zona (espalhado)", "Sul/Sudeste primeiro", "Nordeste primeiro",
            "pequenos primeiro", "aleatório (por zona)", "zonas anti-A primeiro na cidade",
            "zonas pró-A primeiro na cidade"]
ESTRESSE = ["grandes primeiro, zonas anti-A primeiro"]  # extremo: reportado à parte, fora da calibração
_CACHE = {}


def carregar(ano):
    if ano in _CACHE:
        return _CACHE[ano]
    d = np.load(os.path.join(AQUI, "data", f"secoes_{ano}.npz"))
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
        "a1": soma(t1[:, 0]), "b1": soma(t1[:, 1]),
        "validos1": soma(t1[:, :3].sum(1)),
        "comparec1": soma(t1.sum(1)),
        "eleitores": soma(d["aptos"]),
        "capital": np.array([m in capitais for m in muns]),
        "elim": np.column_stack([soma(d["t1_elim"][:, j].astype(float)) for j in range(d["t1_elim"].shape[1])]),
        "nomes_elim": [str(n) for n in d["elim"]],
    }
    zona_id = np.unique(np.char.add(chave, d["zona"].astype(str)), return_inverse=True)[1]
    za1 = np.bincount(zona_id, t1[:, 0]); zb1 = np.bincount(zona_id, t1[:, 1])
    secoes = {"mun_idx": mun_idx, "t2": t2, "aptos": d["aptos"], "zona_id": zona_id,
              "p1_zona": (za1 / np.maximum(za1 + zb1, 1))[zona_id], "fim_zona": d["fim_zona_2t"]}
    verdade = t2[:, 0].sum() / t2[:, :2].sum()
    _CACHE[ano] = (base, secoes, verdade)
    return _CACHE[ano]


def _rank_na_cidade(secoes, sentido):
    """Posição (0 a 1) da zona dentro da cidade, ordenada pela fatia de A no 1º turno."""
    mun, z, p = secoes["mun_idx"], secoes["zona_id"], secoes["p1_zona"]
    zonas, primeira = np.unique(z, return_index=True)
    zm, zp = mun[primeira], sentido * p[primeira]
    ordem = np.lexsort((zp, zm))
    pos = np.empty(len(zonas)); n_por_mun = np.bincount(zm)
    inicio = np.concatenate([[0], np.cumsum(n_por_mun)[:-1]])
    rank = np.arange(len(zonas)) - inicio[zm[ordem]]
    pos[ordem] = rank / np.maximum(n_por_mun[zm[ordem]] - 1, 1)
    return pos[np.searchsorted(zonas, z)]


def tempos_de_chegada(cenario, base, secoes, rng):
    """Um 'horário' de chegada por seção; só a ordem importa."""
    mun = secoes["mun_idx"]
    n = len(mun)
    ruido_zona = rng.random(secoes["zona_id"].max() + 1)[secoes["zona_id"]]
    ruido_mun = rng.random(len(base["a1"]))[mun]
    u = rng.random(n)
    porte = np.log10(np.maximum(base["eleitores"][mun], 100))
    fim = np.where(np.isnan(secoes["fim_zona"]), np.nanmedian(secoes["fim_zona"]), secoes["fim_zona"])
    reg = base["regiao"][mun]
    if cenario == "fim da zona (zona inteira)":
        return fim + 0.01 * u
    if cenario == "fim da zona (espalhado)":
        return u * np.maximum(fim, 1)
    if cenario == "Sul/Sudeste primeiro":
        atraso = {"S": 0.0, "SE": 0.15, "CO": 0.2, "N": 0.5, "NE": 0.7, "EX": 0.9}
        return np.vectorize(atraso.get)(reg) + 0.6 * ruido_zona + 0.3 * u
    if cenario == "Nordeste primeiro":
        atraso = {"NE": 0.0, "N": 0.15, "CO": 0.4, "S": 0.5, "SE": 0.6, "EX": 0.9}
        return np.vectorize(atraso.get)(reg) + 0.6 * ruido_zona + 0.3 * u
    if cenario == "pequenos primeiro":
        return porte / 7 + 0.3 * ruido_zona + 0.2 * u
    if cenario == "aleatório (por zona)":
        return ruido_zona + 0.3 * u
    if cenario == "zonas anti-A primeiro na cidade":
        return 0.6 * ruido_mun + 0.4 * _rank_na_cidade(secoes, +1) + 0.05 * u
    if cenario == "zonas pró-A primeiro na cidade":
        return 0.6 * ruido_mun + 0.4 * _rank_na_cidade(secoes, -1) + 0.05 * u
    if cenario == "grandes primeiro, zonas anti-A primeiro":
        return 0.6 * (1 - (porte - 2) / 5) + 0.4 * _rank_na_cidade(secoes, +1) + 0.05 * u
    raise ValueError(cenario)


def apurado_ate(chegou, base, secoes):
    m, t2 = secoes["mun_idx"][chegou], secoes["t2"][chegou]
    n = len(base["a1"])
    return {"a2": np.bincount(m, t2[:, 0], n), "b2": np.bincount(m, t2[:, 1], n),
            "comparec2": np.bincount(m, t2.sum(1), n),
            "eleitores_apurados": np.bincount(m, secoes["aptos"][chegou], n)}


def _quantis(r):
    pr = r["projecao"]
    if "p25" in pr:
        return pr
    sd = (pr["p95"] - pr["p05"]) / 3.29  # v1 só devolve 5/50/95: aproxima por normal
    return {**pr, "p25": pr["p50"] - 0.674 * sd, "p75": pr["p50"] + 0.674 * sd,
            "p10": pr["p50"] - 1.2816 * sd, "p90": pr["p50"] + 1.2816 * sd}


def avaliar(tarefa):
    """Roda uma eleição × cenário em todos os pontos de checagem."""
    ano, cenario, versao, params, sims, semente = tarefa
    base, secoes, verdade = carregar(ano)
    rng = np.random.default_rng(semente)
    t = tempos_de_chegada(cenario, base, secoes, rng)
    proj = modelo.projetar if versao == 2 else modelo_v1.projetar
    linhas = []
    for c in CHECKPOINTS:
        chegou = t <= np.quantile(t, c)
        r = proj(base, apurado_ate(chegou, base, secoes), params if versao == 2 else None, n_sim=sims)
        q = _quantis(r)
        ganhou_a = verdade > 0.5
        p_venc = r["prob_a"] if ganhou_a else 1 - r["prob_a"]

        def score(lo, hi, alfa):  # interval score: largura + penalidade por ficar fora
            return (hi - lo) + (2 / alfa) * max(lo - verdade, 0) + (2 / alfa) * max(verdade - hi, 0)

        linhas.append({
            "ano": ano, "cenario": cenario, "apurado": c, "versao": versao,
            "ingenuo": r["apurado"]["fatia_a"] - verdade, "erro": q["p50"] - verdade,
            "cobre50": q["p25"] <= verdade <= q["p75"], "cobre80": q["p10"] <= verdade <= q["p90"],
            "cobre90": q["p05"] <= verdade <= q["p95"], "largura90": q["p95"] - q["p05"],
            "score": score(q["p05"], q["p95"], 0.1) + score(q["p25"], q["p75"], 0.5),
            "p_vencedor": p_venc, "chamada_errada": p_venc <= 0.05, "brier": (1 - p_venc) ** 2,
        })
    return linhas


def rodar(anos, cenarios, versoes, params, sims, workers=8):
    tarefas = [(a, c, v, params, sims, 1000 * a + i) for a in anos for i, c in enumerate(cenarios) for v in versoes]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [l for bloco in pool.map(avaliar, tarefas) for l in bloco]


def resumo(linhas):
    if not linhas:
        return {}
    f = lambda k: float(np.mean([l[k] for l in linhas]))
    return {"n": len(linhas), "erro_medio_abs": float(np.mean([abs(l["erro"]) for l in linhas])),
            "erro_max_abs": float(np.max([abs(l["erro"]) for l in linhas])),
            "ingenuo_medio_abs": float(np.mean([abs(l["ingenuo"]) for l in linhas])),
            "cobre50": f("cobre50"), "cobre80": f("cobre80"), "cobre90": f("cobre90"),
            "largura90": f("largura90"), "score": f("score"), "brier": f("brier"),
            "chamadas_erradas": int(sum(l["chamada_errada"] for l in linhas))}


def imprimir(titulo, linhas):
    r = resumo(linhas)
    print(f"{titulo:46} n={r['n']:3}  erro {r['erro_medio_abs']*100:.2f} (máx {r['erro_max_abs']*100:.2f}) "
          f"| ingênuo {r['ingenuo_medio_abs']*100:.2f} | cobre 50/80/90: {r['cobre50']:.0%}/{r['cobre80']:.0%}/{r['cobre90']:.0%} "
          f"| larg90 {r['largura90']*100:.2f} | score {r['score']*100:.2f} | brier {r['brier']:.3f} | erradas {r['chamadas_erradas']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anos", default="2014,2018,2022")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--so-v2", action="store_true")
    ap.add_argument("--calibrar", action="store_true", help="grade de parâmetros em 2014 e 2018 (treino)")
    args = ap.parse_args()
    anos = [int(a) for a in args.anos.split(",")]
    params = json.loads(args.params)

    if args.calibrar:
        # 1ª rodada (72 combinações): sigma_uf {0,5; 1; 1,5 p.p.}, tardio {0,3; 0,6; 1,2}, k {0; 0,5},
        # parcial {1,5; 4}, eliminados separados ou não. Venceu: 0,5 / 1,2 / 0 / 1,5 / separados.
        # 2ª rodada, em volta do vencedor (o tardio estava na borda da grade):
        grade = {"sigma_uf": [0.005, 0.008], "sigma_tardio_nac": [0.012, 0.018, 0.025],
                 "k_tardio": [0.0], "sigma_parcial": [0.0075, 0.015], "usar_elim": [True]}
        resultados = []
        for combo in itertools.product(*grade.values()):
            p = dict(zip(grade, combo))
            p["sigma_regiao"] = p["sigma_uf"]
            p["sigma_tardio_uf"] = p["sigma_tardio_nac"]
            r = resumo(rodar([2014, 2018], CENARIOS, [2], {**params, **p}, args.sims))
            resultados.append((r["score"] + 0.02 * r["chamadas_erradas"], p, r))
            print(f"{json.dumps(p)}  score {r['score']*100:.3f}  cobre90 {r['cobre90']:.0%} cobre50 {r['cobre50']:.0%} "
                  f"erro {r['erro_medio_abs']*100:.2f} erradas {r['chamadas_erradas']}", flush=True)
        melhor = min(resultados, key=lambda x: x[0])
        print("\nmelhor no treino (2014+2018):", json.dumps(melhor[1]))
        return

    versoes = [2] if args.so_v2 else [1, 2]
    linhas = rodar(anos, CENARIOS + ESTRESSE, versoes, params, args.sims)
    for v in versoes:
        print(f"\n===== versão {v} =====")
        for ano in anos:
            imprimir(f"{ano} (cenários normais e adversos)", [l for l in linhas if l["versao"] == v and l["ano"] == ano and l["cenario"] in CENARIOS])
        imprimir("TODAS (sem o estresse)", [l for l in linhas if l["versao"] == v and l["cenario"] in CENARIOS])
        for c in CENARIOS + ESTRESSE:
            imprimir(f"  {c}", [l for l in linhas if l["versao"] == v and l["cenario"] == c])
    with open(os.path.join(AQUI, "data", "backtest.json"), "w") as f:
        json.dump(linhas, f, default=float)


if __name__ == "__main__":
    main()
