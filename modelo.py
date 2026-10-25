"""Projeção do 2º turno a partir da virada de cada município sobre o 1º turno (versão 2).

Ideia em três partes:

1. Virada: em cada município já apurado, mede quanto a fatia do candidato A entre os
   dois finalistas mudou em relação ao 1º turno (virada = A2T/(A2T+B2T) − A1T/(A1T+B1T)).
   Um modelo misto explica essa virada por características do 1º turno (quanto o A tinha,
   quanto foi para cada eliminado relevante, porte) mais um efeito de região e de UF,
   encolhidos para a média quando ainda há pouco dado no lugar.
2. Comparecimento: em cada município apurado, mede quantos votos válidos saíram por
   eleitor em relação ao 1º turno e projeta isso para o que falta, com o mesmo
   encolhimento (município → UF → região → país).
3. Monte Carlo: sorteia os parâmetros da posterior conjunta, um erro comum por UF e um
   nacional para o que falta (maior quando o que falta é diferente do que já chegou), um
   choque de comparecimento por UF, e conta em quantos cenários A passa de 50% dos válidos.

O que mudou da versão 1 (modelo_v1.py), a partir da revisão em docs/revisao-metodologia.md:
- municípios parcialmente apurados têm um termo próprio: o modelo estima, junto com o resto,
  quanto o pedaço já apurado deles destoa do que se espera, e esse viés não vai para o que falta;
- a heterogeneidade entre municípios depende do porte (σ² = s∞² + c/eleitores), estimada com peso;
- cada eliminado com ≥ 1% dos válidos entra como covariável própria, em vez de um bloco só;
- o erro do que falta cresce com a diferença prevista entre o que falta e o que já chegou;
- os desvios de região e UF e o erro do que falta foram calibrados em 2014 e 2018 (backtest.py).

Entradas são arrays por município, na mesma ordem em `base` e `apurado`:
  base:    uf, regiao, a1, b1, validos1, eleitores, capital, [elim (n × k), comparec1]
  apurado: a2, b2, eleitores_apurados, [comparec2]
"""

import numpy as np

PARAMS = {
    "sigma_regiao": 0.008,  # desvio dos efeitos de região na virada (calibrado)
    "sigma_uf": 0.008,  # desvio dos efeitos de UF dentro da região (calibrado)
    "s_inf": 0.01,  # heterogeneidade entre municípios: σ² = s_inf² + c / eleitores (+ 0,25/votos);
    "c_porte": 0.0,  # valores iniciais, reestimados a cada leitura (2014: 2,2 p.p.; 2018: 1,4; 2022: 0,8)
    "s_inf_min": 0.002,
    "sigma_zona": 0.02,  # o pedaço apurado de um município não é idêntico ao todo
    "sigma_parcial": 0.0075,  # prior do viés comum dos municípios parcialmente apurados (calibrado)
    "sd_prior_beta": 0.10,  # prior dos coeficientes das covariáveis de voto
    "sd_prior_porte": 0.03,
    "sd_prior_intercepto": 0.05,
    "sigma_tardio_uf": 0.012,  # erro sistemático do que falta, por UF (calibrado)
    "sigma_tardio_nac": 0.012,  # e comum ao país (calibrado)
    "k_tardio": 0.0,  # crescer com a diferença prevista apurado × falta não ajudou na calibração
    "kappa_comp_uf": 200_000.0,  # votos esperados para confiar no comparecimento da UF
    "kappa_comp_mun": 5_000.0,  # idem para o município
    "sd_comp_uf": 0.03,  # incerteza do comparecimento de UF sem dado
    "sd_comp_nac": 0.02,
    "min_votos": 20,
    "min_elim": 0.01,  # eliminados com pelo menos 1% dos válidos viram covariável própria
}


def _covariaveis(base, p):
    a1, b1 = base["a1"], base["b1"]
    dois = np.maximum(a1 + b1, 1)
    validos = np.maximum(base["validos1"], 1)
    p1 = a1 / dois
    c = p1 - 0.5
    outros = 1 - dois / validos
    colunas, nomes = [np.ones_like(p1), c], ["intercepto", "base_a"]
    blocos = []
    if base.get("elim") is not None and p.get("usar_elim", True):
        elim = np.asarray(base["elim"], float)
        nomes_elim = list(base.get("nomes_elim") or [str(i) for i in range(elim.shape[1])])
        fatia_nac = elim.sum(0) / validos.sum()
        usados = np.flatnonzero(fatia_nac >= p["min_elim"])
        for j in usados:
            blocos.append((f"elim_{nomes_elim[j]}", elim[:, j] / validos))
        resto = outros - sum(elim[:, j] / validos for j in usados) if len(usados) else outros
        blocos.append(("elim_resto", resto))
    else:
        blocos.append(("outros", outros))
    for nome, fatia in blocos:
        media = np.sum(fatia * validos) / validos.sum()
        colunas += [fatia - media, (fatia - media) * c]
        nomes += [nome, f"{nome}_x_base"]
    porte = np.log10(np.maximum(base["eleitores"], 100)) - 4
    colunas += [porte, np.asarray(base["capital"], float)]
    nomes += ["porte", "capital"]
    tipos = ["intercepto", "voto"] + ["voto"] * (2 * len(blocos)) + ["porte", "porte"]
    return np.column_stack(colunas), p1, nomes, tipos


def _encolher(num, den, kappa, alvo):
    """Razão num/den puxada para `alvo` com peso `kappa` (em unidades de den)."""
    return (num + kappa * alvo) / (den + kappa)


def cenario_virada_uniforme(base, virada, excluir=None):
    """Fatia final de A se todo município (fora de `excluir`) tivesse a mesma virada sobre o 1º turno.

    Pondera pelos votos dos dois finalistas no 1º turno, então virada=0 devolve exatamente
    a fatia de A entre os dois no 1º turno.
    """
    p1 = base["a1"] / np.maximum(base["a1"] + base["b1"], 1)
    peso = (base["a1"] + base["b1"]) * (np.asarray(base["uf"]) != excluir)
    return float(np.sum(np.clip(p1 + virada, 0, 1) * peso) / peso.sum())


def _sigma_mun2(eleitores, s_inf, c):
    return s_inf ** 2 + c / np.maximum(eleitores, 100)


def projetar(base, apurado, params=None, n_sim=10_000, seed=0):
    p = {**PARAMS, **(params or {})}
    rng = np.random.default_rng(seed)
    uf, regiao = np.asarray(base["uf"]), np.asarray(base["regiao"])
    ufs, uf_idx = np.unique(uf, return_inverse=True)
    regioes, reg_idx = np.unique(regiao, return_inverse=True)
    reg_da_uf = np.array([reg_idx[np.argmax(uf_idx == i)] for i in range(len(ufs))])

    X, p1, nomes_beta, tipos_beta = _covariaveis(base, p)
    eleitores = base["eleitores"].astype(float)
    validos_por_eleitor1 = base["validos1"] / np.maximum(eleitores, 1)

    a2 = apurado["a2"].astype(float)
    b2 = apurado["b2"].astype(float)
    v2 = a2 + b2
    el_ap = np.minimum(apurado["eleitores_apurados"].astype(float), eleitores)
    frac = np.where(eleitores > 0, el_ap / np.maximum(eleitores, 1), 0)
    frac_nac = el_ap.sum() / max(eleitores.sum(), 1)

    # ---------- comparecimento (votos válidos por eleitor, 2T vs 1T) ----------
    esperado = el_ap * validos_por_eleitor1  # válidos que o 1º turno "previa" no que já foi apurado
    k_nac = v2.sum() / esperado.sum() if esperado.sum() > 0 else 1.0
    k_reg = np.array([_encolher(v2[reg_idx == r].sum(), esperado[reg_idx == r].sum(),
                                p["kappa_comp_uf"], k_nac) for r in range(len(regioes))])
    num_uf = np.bincount(uf_idx, v2, len(ufs))
    den_uf = np.bincount(uf_idx, esperado, len(ufs))
    k_uf = _encolher(num_uf, den_uf, p["kappa_comp_uf"], k_reg[reg_da_uf])
    k_mun = _encolher(v2, esperado, p["kappa_comp_mun"], k_uf[uf_idx])
    peso_dado_uf = den_uf / (den_uf + p["kappa_comp_uf"])
    sd_k_uf = p["sd_comp_uf"] * np.sqrt(1 - peso_dado_uf)

    comparec_ratio = None
    if "comparec2" in apurado and "comparec1" in base:
        c_esp = el_ap * base["comparec1"] / np.maximum(eleitores, 1)
        comparec_ratio = (np.bincount(uf_idx, apurado["comparec2"], len(ufs)),
                          np.bincount(uf_idx, c_esp, len(ufs)))

    # ---------- virada: modelo misto com efeitos de região e UF ----------
    obs = v2 >= p["min_votos"]
    parcial = obs & (frac < 0.999)
    x_parcial = np.where(parcial, 1 - frac, 0.0)  # o viés de composição some quando o município fecha
    y = np.where(obs, a2 / np.maximum(v2, 1) - p1, 0.0)
    n_beta = X.shape[1]
    col_parcial = n_beta
    n_par = n_beta + 1 + len(regioes) + len(ufs)

    def desenho(idx, observacao):
        Z = np.zeros((len(idx), n_par))
        Z[:, :n_beta] = X[idx]
        if observacao:  # o termo de composição só existe no que foi observado, nunca no que falta
            Z[:, col_parcial] = x_parcial[idx]
        Z[np.arange(len(idx)), n_beta + 1 + reg_idx[idx]] = 1
        Z[np.arange(len(idx)), n_beta + 1 + len(regioes) + uf_idx[idx]] = 1
        return Z

    sd_beta = {"intercepto": p["sd_prior_intercepto"], "voto": p["sd_prior_beta"], "porte": p["sd_prior_porte"]}
    precisao_prior = np.concatenate([
        [1 / sd_beta[t] ** 2 for t in tipos_beta],
        [1 / p["sigma_parcial"] ** 2],
        np.full(len(regioes), 1 / p["sigma_regiao"] ** 2),
        np.full(len(ufs), 1 / p["sigma_uf"] ** 2),
    ])

    i_obs = np.flatnonzero(obs)
    Zo = desenho(i_obs, True)
    yo = y[i_obs]
    ruido_obs = 0.25 / np.maximum(v2[i_obs], 1) + p["sigma_zona"] ** 2 * (1 - frac[i_obs])
    s_inf, c_porte = p["s_inf"], p["c_porte"]
    for _ in range(3):
        sig2 = _sigma_mun2(eleitores[i_obs], s_inf, c_porte)
        w = 1 / (sig2 + ruido_obs)
        A = Zo.T @ (Zo * w[:, None]) + np.diag(precisao_prior)
        cov = np.linalg.inv(A)
        theta = cov @ (Zo.T @ (w * yo))
        if len(i_obs) < 100:
            break
        # σ² = s_inf² + c/eleitores por mínimos quadrados nos resíduos ao quadrado, um peso por
        # município (sem isso as cidades grandes, de resíduo pequeno, apagariam o termo c)
        resid2 = (yo - Zo @ theta) ** 2 - ruido_obs
        M = np.column_stack([np.ones_like(resid2), 1 / np.maximum(eleitores[i_obs], 100)])
        coef = np.linalg.lstsq(M, resid2, rcond=None)[0]
        s_inf = float(np.sqrt(max(coef[0], p["s_inf_min"] ** 2)))
        c_porte = float(max(coef[1], 0.0))

    # ---------- o que falta: votos esperados e fatia de A em cada município ----------
    falta_el = np.maximum(eleitores - el_ap, 0)
    falta_v = falta_el * validos_por_eleitor1 * k_mun
    todos = np.arange(len(p1))
    Zp = desenho(todos, False)
    pred = Zp @ theta
    sig2_todos = _sigma_mun2(eleitores, s_inf, c_porte)
    lam = np.zeros_like(p1)
    ruido_todos = np.zeros_like(p1)
    ruido_todos[i_obs] = ruido_obs
    lam[i_obs] = sig2_todos[i_obs] / (sig2_todos[i_obs] + ruido_obs)
    # parte do que falta que vem da virada observada no próprio município (sem o viés de composição)
    fixo = falta_v * (p1 + lam * y)
    G_mun = Zp * (1 - lam)[:, None]
    G_mun[:, col_parcial] -= lam * x_parcial
    var_idio = falta_v ** 2 * (sig2_todos * (1 - lam) ** 2 + p["sigma_zona"] ** 2 * (lam > 0) * (1 - frac))

    G = np.zeros((len(ufs), n_par))
    np.add.at(G, uf_idx, G_mun * falta_v[:, None])
    A_uf = np.bincount(uf_idx, fixo, len(ufs))
    W_uf = np.bincount(uf_idx, falta_v, len(ufs))
    idio_uf = np.sqrt(np.bincount(uf_idx, var_idio, len(ufs)))
    a2_uf = np.bincount(uf_idx, a2, len(ufs))
    v2_uf = np.bincount(uf_idx, v2, len(ufs))

    # erro do que falta: cresce com a diferença prevista entre o que falta e o que já chegou
    def media(x, peso):
        return float(np.sum(x * peso) / peso.sum()) if peso.sum() > 0 else 0.0

    delta_nac = abs(media(pred, falta_v) - media(pred, v2)) if v2.sum() > 0 else 0.0
    delta_uf = np.array([abs(media(pred[uf_idx == i], falta_v[uf_idx == i]) - media(pred[uf_idx == i], v2[uf_idx == i]))
                         if v2[uf_idx == i].sum() > 0 else delta_nac for i in range(len(ufs))])
    sd_tardio_nac = np.hypot(p["sigma_tardio_nac"], p["k_tardio"] * delta_nac)
    sd_tardio_uf = np.hypot(p["sigma_tardio_uf"], p["k_tardio"] * delta_uf)

    # ---------- Monte Carlo ----------
    L = np.linalg.cholesky(cov + 1e-12 * np.eye(n_par))
    thetas = theta[None, :] + rng.standard_normal((n_sim, n_par)) @ L.T
    tardio = (rng.standard_normal((n_sim, len(ufs))) * sd_tardio_uf[None, :]
              + rng.standard_normal((n_sim, 1)) * sd_tardio_nac)
    choque_k = 1 + (rng.standard_normal((n_sim, len(ufs))) * sd_k_uf[None, :]
                    + rng.standard_normal((n_sim, 1)) * p["sd_comp_nac"] * (1 - frac_nac))
    choque_k = np.maximum(choque_k, 0.5)
    a_falta = (A_uf[None, :] + thetas @ G.T + rng.standard_normal((n_sim, len(ufs))) * idio_uf[None, :]
               + tardio * W_uf[None, :]) * choque_k
    v_falta = W_uf[None, :] * choque_k
    a_uf_sim = a2_uf[None, :] + a_falta
    v_uf_sim = v2_uf[None, :] + v_falta
    fatia = a_uf_sim.sum(1) / v_uf_sim.sum(1)
    fatia_uf = a_uf_sim / np.maximum(v_uf_sim, 1)

    validos_tot = v_uf_sim.sum(1)
    falta_tot = validos_tot.mean() - v2.sum()
    resta_algo = v2.sum() > 0 and falta_tot > 0.001 * validos_tot.mean()  # sem isso a conta divide por ~0
    precisa = (0.5 * validos_tot.mean() - a2.sum()) / falta_tot if resta_algo else None
    base_dois = base["a1"].sum() / (base["a1"] + base["b1"]).sum()
    a1_uf = np.bincount(uf_idx, base["a1"], len(ufs))
    b1_uf = np.bincount(uf_idx, base["b1"], len(ufs))
    p1_v2 = np.bincount(uf_idx, p1 * v2, len(ufs))  # base do 1T ponderada pelo que já foi apurado
    fechados = obs & ~parcial
    pct = [5, 10, 25, 50, 75, 90, 95]
    qs = dict(zip(["p05", "p10", "p25", "p50", "p75", "p90", "p95"], map(float, np.percentile(fatia, pct))))

    saida = {
        "versao": 2,
        "prob_a": float((fatia > 0.5).mean()),
        "projecao": {**qs, "media": float(fatia.mean())},
        "apurado": {
            "a": float(a2.sum()), "b": float(b2.sum()),
            "fatia_a": float(a2.sum() / v2.sum()) if v2.sum() else None,
            "frac_eleitores": float(frac_nac),
            "municipios_com_dado": int(obs.sum()),
            "municipios_fechados": int(fechados.sum()),
            "ufs_com_dado": int(len(np.unique(uf_idx[obs]))),
        },
        "validos_projetados": float(validos_tot.mean()),
        "precisa_do_que_falta": float(precisa) if resta_algo else None,
        "fatia_a_no_que_falta": float(a_falta.sum(1).mean() / v_falta.sum(1).mean()) if resta_algo else None,
        "base_1t_dois": float(base_dois),
        "virada_observada": float(np.sum(y * v2 * obs) / v2[obs].sum()) if obs.any() else None,
        "virada_fechados": float(np.sum(y * v2 * fechados) / v2[fechados].sum()) if fechados.any() else None,
        "virada_projetada": float(fatia.mean() - base_dois),
        "comparecimento_razao": float(comparec_ratio[0].sum() / comparec_ratio[1].sum())
        if comparec_ratio is not None and comparec_ratio[1].sum() > 0 else None,
        "validos_por_eleitor_razao": float(k_nac),
        "modelo": {
            "s_inf": s_inf, "c_porte": c_porte,
            "beta": dict(zip(nomes_beta, map(float, theta[:n_beta]))),
            "vies_parciais": float(theta[col_parcial]),
            "regiao": dict(zip(regioes.tolist(), map(float, theta[n_beta + 1:n_beta + 1 + len(regioes)]))),
            "sd_tardio_nac": float(sd_tardio_nac),
        },
        "uf": [],
    }
    lo_uf, med_uf, hi_uf = np.percentile(fatia_uf, [5, 50, 95], axis=0)
    for i, sigla in enumerate(ufs):
        e_uf = eleitores[uf_idx == i].sum()
        saida["uf"].append({
            "uf": sigla,
            "regiao": regioes[reg_da_uf[i]],
            "frac_eleitores": float(el_ap[uf_idx == i].sum() / max(e_uf, 1)),
            "fatia_a_apurada": float(a2_uf[i] / v2_uf[i]) if v2_uf[i] else None,
            "base_1t_dois": float(a1_uf[i] / max(a1_uf[i] + b1_uf[i], 1)),
            "virada_observada": float(a2_uf[i] / v2_uf[i] - p1_v2[i] / v2_uf[i]) if v2_uf[i] >= 50 else None,
            "projecao": {"p05": float(lo_uf[i]), "p50": float(med_uf[i]), "p95": float(hi_uf[i])},
            "prob_a": float((fatia_uf[:, i] > 0.5).mean()),
            "validos_projetados": float(v_uf_sim[:, i].mean()),
            "comparecimento_razao": float(comparec_ratio[0][i] / comparec_ratio[1][i])
            if comparec_ratio is not None and comparec_ratio[1][i] > 0 else None,
        })
    return saida
