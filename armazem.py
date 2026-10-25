"""O Redis do site: onde o poller publica o live.json e guarda o que precisa sobreviver a
um reinício (o estado de cada município e os boletins do exterior já lidos).

Aceita REDIS_URL (protocolo Redis, como o Redis Cloud da Vercel) ou a API REST da Upstash
(KV_REST_API_URL + KV_REST_API_TOKEN). As variáveis vêm do ambiente ou de um .env.local.
"""

import json
import os
import urllib.request

import redis_tcp


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


class Armazem:
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

    def set(self, chave, valor):
        if self.tcp:
            resposta = self.tcp.comando("SET", chave, valor)
            if resposta != "OK":
                raise RuntimeError(f"Redis respondeu {resposta!r}")
            return
        req = urllib.request.Request(f"{self.url}/set/{chave}", data=valor, method="POST",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resposta = json.loads(r.read())
        if resposta.get("result") != "OK":
            raise RuntimeError(f"Redis respondeu {resposta}")

    def get(self, chave):
        if self.tcp:
            return self.tcp.comando("GET", chave)
        req = urllib.request.Request(f"{self.url}/get/{chave}", headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            valor = json.loads(r.read()).get("result")
        return valor.encode() if valor is not None else None

    def get_json(self, chave):
        valor = self.get(chave)
        return json.loads(valor) if valor else None
