"""Lê, seção por seção, os boletins de urna do 2º turno no exterior assim que o TSE os publica.

Fora do Brasil as urnas fecham no horário local, então boa parte do exterior já terminou de
votar antes de o TSE começar a divulgar a totalização (depois que a votação termina no Brasil). Os boletins de
cada seção, porém, ficam públicos no site de resultados assim que chegam. Este script baixa
esses boletins, soma os votos por cidade e grava data/exterior_bu.json, que o poller usa na
seção do exterior. Cada boletim só é baixado uma vez.

Uso: python3 exterior_bu.py [--intervalo 60]
"""

import argparse
import datetime as dt
import json
import os
import time
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import armazem
import bu
import tse

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "data", "exterior_bu.json")
UF = "zz"
STATUS_VALIDOS = {"Recebido", "Totalizado"}


def chave(mun, zona, secao):
    return f"{mun}-{zona}-{secao}"


def ler_secao(cliente, mun, zona, secao):
    """Boletim lido da seção, ou None se ela ainda não enviou."""
    p = tse.PLEITO_2T
    try:
        aux, _ = cliente.get(tse.url_aux_secao(p, UF, mun, zona, secao))
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            return None
        raise
    hashes = [h for h in aux.get("hashes", []) if h.get("st") in STATUS_VALIDOS]
    if not hashes:
        return None
    h = hashes[-1]
    nome = next((a["nm"] for a in h["arq"] if a["tp"] == "bu"), None)
    if not nome:
        return None
    dados = cliente.get_bytes(tse.url_arquivo_urna(p, UF, mun, zona, secao, h["hash"], nome))
    r = bu.ler_bu(dados, tse.ELEICAO_2T)
    return {"mun": mun, "a": r["votos"].get(tse.LULA, 0), "b": r["votos"].get(tse.FLAVIO, 0),
            "comparec": r["comparec"], "aptos": r["aptos"], "recebido": f"{h.get('dr', '')} {h.get('hr', '')}"}


def gravar(lidos, total_secoes, nuvem=None):
    cidades = defaultdict(lambda: {"a": 0, "b": 0, "comparec": 0, "aptos": 0, "secoes": 0})
    for r in lidos.values():
        c = cidades[r["mun"]]
        for k in ("a", "b", "comparec", "aptos"):
            c[k] += r[k]
        c["secoes"] += 1
    saida = {
        "atualizado": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "secoes_lidas": len(lidos),
        "secoes_total": total_secoes,
        "cidades": cidades,
        "secoes": lidos,
    }
    corpo = json.dumps(saida, ensure_ascii=False).encode()
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    tmp = SAIDA + ".tmp"
    with open(tmp, "wb") as f:
        f.write(corpo)
    os.replace(tmp, SAIDA)
    if nuvem:  # para a próxima execução (e o poller) retomarem daqui
        try:
            nuvem.set("exterior_bu", corpo)
        except Exception as erro:
            print(f"  falhou ao salvar no Redis: {erro!r}", flush=True)
    a = sum(c["a"] for c in cidades.values())
    b = sum(c["b"] for c in cidades.values())
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervalo", type=float, default=60)
    ap.add_argument("--duracao-min", type=float, default=None, help="encerra depois de N minutos")
    args = ap.parse_args()

    armazem.carregar_env(os.path.join(AQUI, ".env.local"))
    nuvem = armazem.Armazem()
    nuvem = nuvem if nuvem.ativo else None
    cliente = tse.Cliente(max_por_segundo=25)  # somado ao poller (60/s), fica abaixo do limite de 100/s do TSE
    cfg, _ = cliente.get(tse.url_secoes(tse.PLEITO_2T, UF))
    secoes = [(m["cd"], z["cd"], s["ns"]) for abr in cfg["abr"] for m in abr["mu"] for z in m["zon"] for s in z["sec"]]
    anterior = nuvem.get_json("exterior_bu") if nuvem else None
    if anterior is None and os.path.exists(SAIDA):
        with open(SAIDA) as f:
            anterior = json.load(f)
    lidos = (anterior or {}).get("secoes", {})
    print(f"retomando com {len(lidos)} seções já lidas", flush=True)

    fim = time.monotonic() + args.duracao_min * 60 if args.duracao_min else None
    while fim is None or time.monotonic() < fim:
        inicio = time.monotonic()
        pendentes = [s for s in secoes if chave(*s) not in lidos]
        erros = 0

        def tentar(s):
            try:
                return s, ler_secao(cliente, *s)
            except Exception as erro:  # um boletim com problema não derruba a rodada
                return s, erro

        with ThreadPoolExecutor(max_workers=16) as pool:
            for s, r in pool.map(tentar, pendentes):
                if isinstance(r, Exception):
                    erros += 1
                elif r is not None:
                    lidos[chave(*s)] = r
        a, b = gravar(lidos, len(secoes), nuvem)
        validos = a + b
        print(f"{dt.datetime.now():%H:%M:%S} {len(lidos)}/{len(secoes)} seções lidas | "
              f"Lula {a:,} ({a / validos:.2%}) × Flávio {b:,} ({b / validos:.2%})" if validos else
              f"{dt.datetime.now():%H:%M:%S} nenhuma seção publicada ainda",
              f"| {erros} erros | {time.monotonic() - inicio:.0f}s", flush=True)
        if len(lidos) == len(secoes):
            break
        time.sleep(max(0, args.intervalo - (time.monotonic() - inicio)))


if __name__ == "__main__":
    main()
