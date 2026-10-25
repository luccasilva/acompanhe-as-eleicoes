"""Acompanha a apuração do 2º turno de 2026 e escreve public/live.json a cada ciclo.

Uso:
  python3 poller.py                 # ao vivo, consultando o TSE a cada 30s
  python3 poller.py --ensaio        # 2º turno simulado em cima do 1º turno real de 2026

A cada ciclo: baixa os 28 arquivos de andamento por UF, descobre quais municípios
avançaram, baixa só esses, roda o modelo e regrava o JSON que a página lê.
"""

import argparse
import datetime as dt
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import modelo
import redis_tcp
import tse

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE_1T = os.path.join(AQUI, "data", "base_1t_2026.json")
SAIDA = os.path.join(AQUI, "public", "live.json")
ARQ_BU_EXTERIOR = os.path.join(AQUI, "data", "exterior_bu.json")

NOME_UF = {
    "ac": "Acre", "al": "Alagoas", "am": "Amazonas", "ap": "Amapá", "ba": "Bahia", "ce": "Ceará",
    "df": "Distrito Federal", "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão", "mg": "Minas Gerais",
    "ms": "Mato Grosso do Sul", "mt": "Mato Grosso", "pa": "Pará", "pb": "Paraíba", "pe": "Pernambuco",
    "pi": "Piauí", "pr": "Paraná", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte", "ro": "Rondônia",
    "rr": "Roraima", "rs": "Rio Grande do Sul", "sc": "Santa Catarina", "se": "Sergipe", "sp": "São Paulo",
    "to": "Tocantins", "zz": "Exterior",
}


def carregar_base():
    with open(BASE_1T) as f:
        muns = json.load(f)["municipios"]
    base = {
        "uf": np.array([m["uf"] for m in muns]),
        "regiao": np.array([m["regiao"] for m in muns]),
        "a1": np.array([m["lula"] for m in muns], float),
        "b1": np.array([m["flavio"] for m in muns], float),
        "validos1": np.array([m["validos"] for m in muns], float),
        "comparec1": np.array([m["comparec"] for m in muns], float),
        "eleitores": np.array([m["eleitores"] for m in muns], float),
        "capital": np.array([m["capital"] for m in muns]),
    }
    return muns, base


class FonteTSE:
    """Estado do 2º turno por município, atualizado só onde o andamento mudou."""

    def __init__(self, muns, max_por_ciclo=1500):
        self.cliente = tse.Cliente(max_por_segundo=60)
        self.muns = muns
        self.indice = {(m["uf"], m["cd"]): i for i, m in enumerate(muns)}
        self.secoes_vistas = np.zeros(len(muns), int)
        self.max_por_ciclo = max_por_ciclo
        n = len(muns)
        self.a2, self.b2, self.comparec2, self.el_ap = (np.zeros(n) for _ in range(4))
        self.oficial = None

    def atualizar(self):
        e = tse.ELEICAO_2T

        def andamento(uf):
            try:
                return uf, self.cliente.get(tse.url_andamento(e, uf))[0]
            except Exception as erro:  # um arquivo fora do ar não derruba o ciclo
                print(f"  andamento {uf}: {erro}")
                return uf, None

        with ThreadPoolExecutor(max_workers=16) as pool:
            br = pool.submit(self.cliente.get, tse.url_abrangencia(e, "br"))
            andamentos = list(pool.map(andamento, tse.UFS))
            self.oficial = tse.resumo(br.result()[0])

        mudou = []
        for uf, dado in andamentos:
            for abr in (dado or {}).get("abr", []):
                i = self.indice.get((uf, abr["cdabr"]))
                if i is not None and int(abr["s"]["st"]) != self.secoes_vistas[i]:
                    mudou.append(i)
        mudou.sort(key=lambda i: -self.muns[i]["eleitores"])
        pendentes = len(mudou) - self.max_por_ciclo
        mudou = mudou[: self.max_por_ciclo]

        def baixar(i):
            m = self.muns[i]
            try:
                return i, tse.resumo(self.cliente.get(tse.url_abrangencia(e, m["uf"], m["cd"]))[0])
            except Exception as erro:
                print(f"  {m['uf']}{m['cd']}: {erro}")
                return i, None

        with ThreadPoolExecutor(max_workers=16) as pool:
            for i, r in pool.map(baixar, mudou):
                if r is None:
                    continue
                self.a2[i] = r["cand"].get(tse.LULA, 0)
                self.b2[i] = r["cand"].get(tse.FLAVIO, 0)
                self.comparec2[i] = r["comparec"]
                self.el_ap[i] = r["eleitores_tot"]
                self.secoes_vistas[i] = r["secoes_tot"]
        o = self.oficial
        return {
            "apurado": self._apurado(),
            "oficial": {
                "secoes_frac": o["secoes_tot"] / max(o["secoes"], 1),
                "a": o["cand"].get(tse.LULA, 0),
                "b": o["cand"].get(tse.FLAVIO, 0),
                "gerado": o["gerado"],
                "final": o["final"],
            },
            "log": f"{len(mudou)} municípios baixados" + (f", {pendentes} ficam pro próximo ciclo" if pendentes > 0 else ""),
        }

    def _apurado(self):
        return {"a2": self.a2.copy(), "b2": self.b2.copy(), "comparec2": self.comparec2.copy(),
                "eleitores_apurados": self.el_ap.copy()}


class FonteEnsaio:
    """2º turno inventado sobre a base real do 1º turno, para testar a página antes das 17h.

    Cada município tem uma virada sorteada (nacional + região + UF + município) e um
    horário de chegada que depende da região e do porte; a apuração inteira dura
    `minutos` minutos de relógio.
    """

    def __init__(self, base, alvo=0.503, minutos=12, seed=7):
        rng = np.random.default_rng(seed)
        n = len(base["a1"])
        p1 = base["a1"] / np.maximum(base["a1"] + base["b1"], 1)
        reg = {r: rng.normal(0, 0.012) for r in np.unique(base["regiao"])}
        uf = {u: rng.normal(0, 0.01) for u in np.unique(base["uf"])}
        outros = 1 - (base["a1"] + base["b1"]) / np.maximum(base["validos1"], 1)
        virada_m = (np.vectorize(reg.get)(base["regiao"]) + np.vectorize(uf.get)(base["uf"])
                    - 0.25 * (outros - 0.08) + rng.normal(0, 0.025, n))
        self.validos2 = base["validos1"] * rng.normal(0.97, 0.03, n).clip(0.8, 1.1)
        p2 = p1 + virada_m
        p2 += alvo - (p2 * self.validos2).sum() / self.validos2.sum()  # resultado final do cenário = alvo
        self.p2 = np.clip(p2, 0.02, 0.98)
        self.comparec2 = base["comparec1"] * rng.normal(0.99, 0.02, n).clip(0.85, 1.1)
        self.eleitores = base["eleitores"]
        atraso = {"S": 0.05, "SE": 0.1, "CO": 0.1, "N": 0.25, "NE": 0.3, "EX": 0.5}
        porte = np.log10(np.maximum(self.eleitores, 100))
        self.inicio = np.vectorize(atraso.get)(base["regiao"]) + 0.05 * porte + rng.random(n) * 0.3
        self.duracao = 0.05 + 0.08 * (porte - 2)
        self.desvio_parcial = rng.normal(0, 0.02, n)
        self.t0 = time.monotonic()
        self.minutos = minutos

    def atualizar(self):
        t = (time.monotonic() - self.t0) / (self.minutos * 60) * 1.25
        f = np.clip((t - self.inicio) / self.duracao, 0, 1)
        # o pedaço já apurado de um município é parecido, mas não idêntico, ao todo
        p_parcial = np.clip(self.p2 + self.desvio_parcial * (1 - f), 0, 1)
        v = np.floor(self.validos2 * f)
        a2 = np.round(v * p_parcial)
        ap = {"a2": a2, "b2": v - a2, "comparec2": np.floor(self.comparec2 * f),
              "eleitores_apurados": np.floor(self.eleitores * f)}
        return {
            "apurado": ap,
            "oficial": {"secoes_frac": float(ap["eleitores_apurados"].sum() / self.eleitores.sum()),
                        "a": float(a2.sum()), "b": float(ap["b2"].sum()),
                        "gerado": dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "final": bool(f.min() >= 1)},
            "log": f"ensaio t={t:.2f}",
        }


def carregar_bu_exterior():
    """Boletins de urna do exterior lidos por exterior_bu.py, se ele estiver rodando."""
    try:
        with open(ARQ_BU_EXTERIOR) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def resumo_exterior(muns, base, apurado, bu=None):
    """Brasileiros que votam fora do país: 1º turno, parcial do 2º turno e cada cidade.

    O parcial vem da totalização do TSE ou dos boletins de urna lidos seção por seção,
    o que cobrir mais eleitores (antes das 17h, só os boletins existem).
    """
    zz = np.flatnonzero(base["uf"] == "zz")
    a1, b1 = base["a1"][zz], base["b1"][zz]
    el = base["eleitores"][zz]
    a2, b2 = apurado["a2"][zz].copy(), apurado["b2"][zz].copy()
    comp2, el_ap = apurado["comparec2"][zz].copy(), apurado["eleitores_apurados"][zz].copy()
    fonte, secoes = "totalização do TSE", None
    if bu and sum(c["aptos"] for c in bu["cidades"].values()) > el_ap.sum():
        pos = {muns[i]["cd"]: j for j, i in enumerate(zz)}
        a2[:], b2[:], comp2[:], el_ap[:] = 0, 0, 0, 0
        for cd, c in bu["cidades"].items():
            if cd in pos:
                j = pos[cd]
                a2[j], b2[j], comp2[j], el_ap[j] = c["a"], c["b"], c["comparec"], c["aptos"]
        fonte, secoes = "boletins de urna", {"lidas": bu["secoes_lidas"], "total": bu["secoes_total"]}

    p1 = a1 / np.maximum(a1 + b1, 1)
    v2 = a2 + b2
    obs = v2 >= 20
    virada = float(np.sum(v2[obs] * (a2[obs] / v2[obs] - p1[obs])) / v2[obs].sum()) if obs.any() else None
    comp_esperado = (el_ap * base["comparec1"][zz] / np.maximum(el, 1)).sum()
    cidades = []
    for j in np.argsort(-el):
        fatia2 = a2[j] / v2[j] if obs[j] else None
        cidades.append({
            "nome": muns[zz[j]]["nome"],
            "eleitores": float(el[j]),
            "base_1t_dois": float(p1[j]),
            "frac_eleitores": float(min(el_ap[j] / max(el[j], 1), 1)),
            "fatia_a_apurada": float(fatia2) if fatia2 is not None else None,
            "virada": float(fatia2 - p1[j]) if fatia2 is not None else None,
        })
    return {
        "fonte": fonte,
        "secoes": secoes,
        "primeiro_turno": {
            "a_pct": float(a1.sum() / base["validos1"][zz].sum()),
            "b_pct": float(b1.sum() / base["validos1"][zz].sum()),
            "base_dois": float(a1.sum() / (a1 + b1).sum()),
            "eleitores": float(el.sum()),
            "comparec": float(base["comparec1"][zz].sum()),
            "peso_validos": float(base["validos1"][zz].sum() / base["validos1"].sum()),
        },
        "segundo_turno": {
            "frac_eleitores": float(min(el_ap.sum() / el.sum(), 1)),
            "a": float(a2.sum()),
            "b": float(b2.sum()),
            "fatia_a": float(a2.sum() / v2.sum()) if v2.sum() else None,
            "virada": virada,
            "comparecimento_razao": float(comp2.sum() / comp_esperado) if comp_esperado > 0 else None,
        },
        "cenario_brasil": modelo.cenario_virada_uniforme(base, virada, excluir="zz") if virada is not None else None,
        "cidades": cidades,
    }


def montar_json(muns, base, leitura, resultado, historico, modo, intervalo):
    of = leitura["oficial"]
    validos_of = of["a"] + of["b"]
    pesos = {u: float(base["eleitores"][base["uf"] == u].sum()) for u in np.unique(base["uf"])}
    for linha in resultado["uf"]:
        linha["nome"] = NOME_UF[linha["uf"]]
        linha["eleitores"] = pesos[linha["uf"]]
    a1, b1, v1 = base["a1"].sum(), base["b1"].sum(), base["validos1"].sum()
    return {
        "atualizado": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "modo": modo,
        "intervalo_s": intervalo,
        "candidatos": {"a": {"nome": "Lula", "numero": "13"}, "b": {"nome": "Flávio Bolsonaro", "numero": "22"}},
        "primeiro_turno": {"a_pct": a1 / v1, "b_pct": b1 / v1, "base_dois": a1 / (a1 + b1)},
        "oficial": {**of, "a_pct": of["a"] / validos_of if validos_of else None},
        "modelo": resultado,
        "exterior": resumo_exterior(muns, base, leitura["apurado"], bu=None if modo == "ensaio" else carregar_bu_exterior()),
        "historico": historico,
    }


def gravar(caminho, dado):
    corpo = json.dumps(dado, ensure_ascii=False, separators=(",", ":")).encode()
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "wb") as f:
        f.write(corpo)
    os.replace(tmp, caminho)
    return corpo


def carregar_env(caminho):
    """Lê KEY=VALOR de um .env.local (o formato que a Vercel mostra), sem sobrescrever o ambiente."""
    if not os.path.exists(caminho):
        return
    with open(caminho) as f:
        for linha in f:
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                chave, valor = linha.split("=", 1)
                os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


class PublicadorRedis:
    """Grava o live.json no Redis que a função api/live.js da Vercel lê.

    Aceita REDIS_URL (protocolo Redis, como o Redis Cloud da Vercel) ou a API REST da
    Upstash (KV_REST_API_URL + KV_REST_API_TOKEN).
    """

    def __init__(self):
        env = os.environ

        def achar(sufixo):  # a Vercel deixa trocar o prefixo KV_ na criação do banco
            return next((v for k, v in env.items() if k.endswith(sufixo) and "READ_ONLY" not in k), None)

        self.url = (env.get("KV_REST_API_URL") or env.get("UPSTASH_REDIS_REST_URL") or achar("_REST_API_URL") or "").rstrip("/")
        self.token = env.get("KV_REST_API_TOKEN") or env.get("UPSTASH_REDIS_REST_TOKEN") or achar("_REST_API_TOKEN")
        url_redis = env.get("REDIS_URL") or env.get("KV_URL")
        self.tcp = redis_tcp.RedisTCP(url_redis) if url_redis and not (self.url and self.token) else None

    @property
    def ativo(self):
        return bool(self.tcp or (self.url and self.token))

    @property
    def destino(self):
        return f"{self.tcp.host}:{self.tcp.porta}" if self.tcp else self.url

    def publicar(self, corpo):
        if self.tcp:
            resposta = self.tcp.comando("SET", "live", corpo)
            if resposta != "OK":
                raise RuntimeError(f"Redis respondeu {resposta!r}")
            return
        req = urllib.request.Request(f"{self.url}/set/live", data=corpo, method="POST",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resposta = json.loads(r.read())
        if resposta.get("result") != "OK":
            raise RuntimeError(f"Redis respondeu {resposta}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensaio", action="store_true", help="simula um 2º turno em cima da base de 2026")
    ap.add_argument("--alvo", type=float, default=0.503, help="ensaio: fatia final do Lula no cenário simulado (0.503 = 50,3%%)")
    ap.add_argument("--minutos", type=float, default=12, help="ensaio: duração da apuração simulada")
    ap.add_argument("--intervalo", type=float, default=30)
    ap.add_argument("--sims", type=int, default=10_000)
    ap.add_argument("--saida", default=None, help="padrão: public/live.json (ou public/ensaio.json no ensaio)")
    args = ap.parse_args()
    args.saida = args.saida or (SAIDA.replace("live.json", "ensaio.json") if args.ensaio else SAIDA)

    muns, base = carregar_base()
    fonte = FonteEnsaio(base, args.alvo, args.minutos) if args.ensaio else FonteTSE(muns)
    modo = "ensaio" if args.ensaio else "ao vivo"
    carregar_env(os.path.join(AQUI, ".env.local"))
    publicador = None if args.ensaio else PublicadorRedis()  # o ensaio nunca vai para o site
    if publicador and publicador.ativo:
        print(f"enviando o live.json para o Redis em {publicador.destino}", flush=True)
    elif not args.ensaio:
        print("sem REDIS_URL (nem KV_REST_API_URL/KV_REST_API_TOKEN): gravando só o arquivo local", flush=True)
    arq_hist = os.path.join(AQUI, "data", f"historico_{'ensaio' if args.ensaio else '2t'}.json")
    historico = []
    if not args.ensaio and os.path.exists(arq_hist):
        with open(arq_hist) as f:
            historico = json.load(f)

    while True:
        inicio = time.monotonic()
        try:
            leitura = fonte.atualizar()
            r = modelo.projetar(base, leitura["apurado"], n_sim=args.sims)
            ponto = {
                "hora": dt.datetime.now().strftime("%H:%M:%S"),
                "secoes_frac": leitura["oficial"]["secoes_frac"],
                "frac": r["apurado"]["frac_eleitores"],
                "prob": r["prob_a"],
                "p05": r["projecao"]["p05"], "p50": r["projecao"]["p50"], "p95": r["projecao"]["p95"],
                "ingenuo": r["apurado"]["fatia_a"],
            }
            if r["apurado"]["frac_eleitores"] > 0 and (not historico or historico[-1]["frac"] != ponto["frac"]):
                historico.append(ponto)
                with open(arq_hist, "w") as f:
                    json.dump(historico, f)
            corpo = gravar(args.saida, montar_json(muns, base, leitura, r, historico, modo, args.intervalo))
            envio = ""
            if publicador and publicador.ativo:
                try:
                    publicador.publicar(corpo)
                    envio = ", enviado ao site"
                except Exception as erro:  # o site fica com a leitura anterior; tenta de novo no próximo ciclo
                    envio = f", FALHOU o envio ao site: {erro!r}"
            print(f"{ponto['hora']} apurado {ponto['secoes_frac']:.1%}  P(Lula) {r['prob_a']:.1%}  "
                  f"projeção {r['projecao']['p50']:.2%} [{r['projecao']['p05']:.2%}, {r['projecao']['p95']:.2%}]  "
                  f"({leitura['log']}, {time.monotonic() - inicio:.1f}s{envio})", flush=True)
            if leitura["oficial"]["final"] and args.ensaio:
                break
        except Exception as erro:
            print(f"ciclo falhou: {erro!r}", flush=True)
        time.sleep(max(0, args.intervalo - (time.monotonic() - inicio)))


if __name__ == "__main__":
    main()
