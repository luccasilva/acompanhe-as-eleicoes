"""Projeção do 2º turno a partir da virada de cada município sobre o 1º turno.

Ideia em três partes:

1. Virada: em cada município já apurado, mede quanto a fatia do candidato A entre os
   dois finalistas mudou em relação ao 1º turno (virada = A2T/(A2T+B2T) − A1T/(A1T+B1T)).
   Um modelo misto explica essa virada por características do 1º turno (quanto o A
   tinha, quanto foi para os eliminados, porte) mais um efeito de região e de UF.
   Os efeitos são encolhidos para a média quando ainda há pouco dado no lugar.
2. Comparecimento: em cada município apurado, mede quantos votos válidos saíram por
   eleitor em relação ao 1º turno e projeta isso para o que falta, com o mesmo
   encolhimento (município → UF → região → país).
3. Monte Carlo: sorteia os parâmetros do modelo da posterior conjunta, um erro comum
   por UF e um nacional (o que falta pode ser sistematicamente diferente do que já
   chegou) e um choque de comparecimento por UF, e conta em quantos cenários A passa
   de 50% dos válidos.

Entradas são arrays por município, na mesma ordem em `base` e `apurado`:
  base:    uf, regiao, a1, b1, validos1, eleitores, capital
  apurado: a2, b2, eleitores_apurados, comparec2 (opcional)
"""

import numpy as np

PARAMS = {
    "sigma_regiao": 0.015,  # desvio dos efeitos de região na virada
    "sigma_uf": 0.015,  # desvio dos efeitos de UF dentro da região
    "sigma_mun_inicial": 0.03,  # heterogeneidade entre municípios (reestimada a cada rodada)
    "sigma_zona": 0.02,  # o pedaço apurado de um município não é idêntico ao que falta
    "sd_prior_beta": 0.10,  # prior dos coeficientes das covariáveis
    "sd_prior_intercepto": 0.05,
    "sigma_tardio_uf": 0.01,  # erro sistemático do que falta, por UF
    "sigma_tardio_nac": 0.01,  # e comum ao país
    "kappa_comp_uf": 200_000.0,  # votos esperados para confiar no comparecimento da UF
    "kappa_comp_mun": 5_000.0,  # idem para o município
    "sd_comp_uf": 0.03,  # incerteza do comparecimento de UF sem dado
    "sd_comp_nac": 0.02,
    "min_votos": 20,
}


def _covariaveis(base):
    a1, b1 = base["a1"], base["b1"]
    dois = np.maximum(a1 + b1, 1)
    p1 = a1 / dois
    outros = 1 - dois / np.maximum(base["validos1"], 1)
    porte = np.log10(np.maximum(base["eleitores"], 100)) - 4
    return np.column_stack([
        np.ones_like(p1),
        p1 - 0.5,
        outros - 0.08,
        (outros - 0.08) * (p1 - 0.5),
        porte,
        base["capital"].astype(float),
    ]), p1


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


def projetar(base, apurado, params=None, n_sim=10_000, seed=0):
    p = {**PARAMS, **(params or {})}
    rng = np.random.default_rng(seed)
    uf, regiao = np.asarray(base["uf"]), np.asarray(base["regiao"])
    ufs, uf_idx = np.unique(uf, return_inverse=True)
    regioes, reg_idx = np.unique(regiao, return_inverse=True)
    reg_da_uf = np.array([reg_idx[np.argmax(uf_idx == i)] for i in range(len(ufs))])

    X, p1 = _covariaveis(base)
    eleitores = base["eleitores"].astype(float)
    validos_por_eleitor1 = base["validos1"] / np.maximum(eleitores, 1)

    a2 = apurado["a2"].astype(float)
    b2 = apurado["b2"].astype(float)
    v2 = a2 + b2
    el_ap = np.minimum(apurado["eleitores_apurados"].astype(float), eleitores)
    frac = np.where(eleitores > 0, el_ap / np.maximum(eleitores, 1), 0)

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
    y = np.where(obs, a2 / np.maximum(v2, 1) - p1, 0.0)
    n_beta = X.shape[1]
    n_par = n_beta + len(regioes) + len(ufs)

    def desenho(idx):
        Z = np.zeros((len(idx), n_par))
        Z[:, :n_beta] = X[idx]
        Z[np.arange(len(idx)), n_beta + reg_idx[idx]] = 1
        Z[np.arange(len(idx)), n_beta + len(regioes) + uf_idx[idx]] = 1
        return Z

    precisao_prior = np.concatenate([
        [1 / p["sd_prior_intercepto"] ** 2],
        np.full(n_beta - 1, 1 / p["sd_prior_beta"] ** 2),
        np.full(len(regioes), 1 / p["sigma_regiao"] ** 2),
        np.full(len(ufs), 1 / p["sigma_uf"] ** 2),
    ])

    i_obs = np.flatnonzero(obs)
    Zo = desenho(i_obs)
    yo = y[i_obs]
    ruido_amostral = 0.25 / np.maximum(v2[i_obs], 1) + p["sigma_zona"] ** 2 * (1 - frac[i_obs])
    sigma_mun = p["sigma_mun_inicial"]
    for _ in range(3):
        w = 1 / (sigma_mun ** 2 + ruido_amostral)
        A = Zo.T @ (Zo * w[:, None]) + np.diag(precisao_prior)
        cov = np.linalg.inv(A)
        theta = cov @ (Zo.T @ (w * yo))
        if len(i_obs) < 30:
            break
        resid = yo - Zo @ theta
        sigma_mun = float(np.sqrt(max(np.average(resid ** 2 - ruido_amostral, weights=w * 0 + 1), 0.005 ** 2)))

    # ---------- o que falta: votos esperados e fatia de A em cada município ----------
    falta_el = np.maximum(eleitores - el_ap, 0)
    falta_v = falta_el * validos_por_eleitor1 * k_mun
    pred = desenho(np.arange(len(p1))) @ theta
    var_pred_obs = sigma_mun ** 2
    lam = np.zeros_like(p1)
    lam[i_obs] = var_pred_obs / (var_pred_obs + ruido_amostral)
    fixo = falta_v * (p1 + lam * y)  # parte que já vem da virada observada no próprio município
    var_idio = falta_v ** 2 * (sigma_mun ** 2 * (1 - lam) ** 2 + p["sigma_zona"] ** 2 * (lam > 0))

    G = np.zeros((len(ufs), n_par))
    np.add.at(G, uf_idx, desenho(np.arange(len(p1))) * (falta_v * (1 - lam))[:, None])
    A_uf = np.bincount(uf_idx, fixo, len(ufs))
    W_uf = np.bincount(uf_idx, falta_v, len(ufs))
    idio_uf = np.sqrt(np.bincount(uf_idx, var_idio, len(ufs)))
    a2_uf = np.bincount(uf_idx, a2, len(ufs))
    v2_uf = np.bincount(uf_idx, v2, len(ufs))

    # ---------- Monte Carlo ----------
    L = np.linalg.cholesky(cov + 1e-12 * np.eye(n_par))
    thetas = theta[None, :] + rng.standard_normal((n_sim, n_par)) @ L.T
    tardio = (rng.standard_normal((n_sim, len(ufs))) * p["sigma_tardio_uf"]
              + rng.standard_normal((n_sim, 1)) * p["sigma_tardio_nac"])
    choque_k = 1 + (rng.standard_normal((n_sim, len(ufs))) * sd_k_uf[None, :]
                    + rng.standard_normal((n_sim, 1)) * p["sd_comp_nac"] * (1 - frac.mean()))
    choque_k = np.maximum(choque_k, 0.5)
    a_falta = (A_uf[None, :] + thetas @ G.T + rng.standard_normal((n_sim, len(ufs))) * idio_uf[None, :]
               + tardio * W_uf[None, :]) * choque_k
    v_falta = W_uf[None, :] * choque_k
    a_uf_sim = a2_uf[None, :] + a_falta
    v_uf_sim = v2_uf[None, :] + v_falta
    fatia = a_uf_sim.sum(1) / v_uf_sim.sum(1)
    fatia_uf = a_uf_sim / np.maximum(v_uf_sim, 1)

    def q(x, axis=0):
        return np.percentile(x, [5, 50, 95], axis=axis)

    validos_tot = v_uf_sim.sum(1)
    falta_tot = validos_tot.mean() - v2.sum()
    resta_algo = v2.sum() > 0 and falta_tot > 0.001 * validos_tot.mean()  # sem isso a conta divide por ~0
    precisa = (0.5 * validos_tot.mean() - a2.sum()) / falta_tot if resta_algo else None
    base_dois = base["a1"].sum() / (base["a1"] + base["b1"]).sum()
    a1_uf = np.bincount(uf_idx, base["a1"], len(ufs))
    b1_uf = np.bincount(uf_idx, base["b1"], len(ufs))
    p1_v2 = np.bincount(uf_idx, p1 * v2, len(ufs))  # base do 1T ponderada pelo que já foi apurado

    lo, med, hi = q(fatia)
    saida = {
        "prob_a": float((fatia > 0.5).mean()),
        "projecao": {"p05": float(lo), "p50": float(med), "p95": float(hi), "media": float(fatia.mean())},
        "apurado": {
            "a": float(a2.sum()), "b": float(b2.sum()),
            "fatia_a": float(a2.sum() / v2.sum()) if v2.sum() else None,
            "frac_eleitores": float(el_ap.sum() / eleitores.sum()),
            "municipios_com_dado": int(obs.sum()),
        },
        "validos_projetados": float(validos_tot.mean()),
        "precisa_do_que_falta": float(precisa) if resta_algo else None,
        "fatia_a_no_que_falta": float(a_falta.sum(1).mean() / v_falta.sum(1).mean()) if resta_algo else None,
        "base_1t_dois": float(base_dois),
        "virada_observada": float(np.sum(y * v2 * obs) / v2[obs].sum()) if obs.any() else None,
        "virada_projetada": float(fatia.mean() - base_dois),
        "comparecimento_razao": float(comparec_ratio[0].sum() / comparec_ratio[1].sum())
        if comparec_ratio is not None and comparec_ratio[1].sum() > 0 else None,
        "validos_por_eleitor_razao": float(k_nac),
        "modelo": {
            "sigma_mun": sigma_mun,
            "beta": dict(zip(["intercepto", "base_a", "outros", "outros_x_base", "porte", "capital"],
                             map(float, theta[:n_beta]))),
            "regiao": dict(zip(regioes.tolist(), map(float, theta[n_beta:n_beta + len(regioes)]))),
        },
        "uf": [],
    }
    lo_uf, med_uf, hi_uf = q(fatia_uf)
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
