"""Cliente mínimo para os JSONs públicos de resultados do TSE (ciclo 2026).

O site resultados.tse.jus.br lê arquivos JSON estáticos; aqui só montamos as
URLs, fazemos GET com ETag (o TSE responde 304 quando nada mudou) e
respeitamos um teto de requisições por segundo (relatado como 100/s por IP).
"""

import json
import threading
import time
import urllib.error
import urllib.request

BASE = "https://resultados.tse.jus.br/oficial/ele2026"
ELEICAO_1T = "6257"
ELEICAO_2T = "6258"
PLEITO_2T = "3221"  # os arquivos de urna (boletim por seção) são organizados por pleito, não por eleição
CARGO_PRESIDENTE = "0001"

LULA = "13"
FLAVIO = "22"

REGIAO = {
    **dict.fromkeys("ac am ap pa ro rr to".split(), "N"),
    **dict.fromkeys("al ba ce ma pb pe pi rn se".split(), "NE"),
    **dict.fromkeys("df go ms mt".split(), "CO"),
    **dict.fromkeys("es mg rj sp".split(), "SE"),
    **dict.fromkeys("pr rs sc".split(), "S"),
    "zz": "EX",
}
UFS = sorted(REGIAO)


def _e(eleicao):
    return eleicao.zfill(6)


def url_municipios(eleicao):
    return f"{BASE}/{eleicao}/config/mun-e{_e(eleicao)}-cm.json"


def url_andamento(eleicao, uf):
    return f"{BASE}/{eleicao}/dados/{uf}/{uf}-e{_e(eleicao)}-ab.json"


def url_abrangencia(eleicao, uf, cd_mun=""):
    """Resultado de presidente do país ("br"), de uma UF ou de um município."""
    return f"{BASE}/{eleicao}/dados/{uf}/{uf}{cd_mun}-c{CARGO_PRESIDENTE}-e{_e(eleicao)}-u.json"


def url_secoes(pleito, uf):
    """Lista de seções (município → zona → seção) de uma UF."""
    return f"{BASE}/arquivo-urna/{pleito}/config/{uf}/{uf}-p{_e(pleito)}-cs.json"


def url_aux_secao(pleito, uf, mun, zona, secao):
    """Situação de uma seção e os arquivos de urna que ela já enviou."""
    return f"{BASE}/arquivo-urna/{pleito}/dados/{uf}/{mun}/{zona}/{secao}/p{_e(pleito)}-{uf}-m{mun}-z{zona}-s{secao}-aux.json"


def url_arquivo_urna(pleito, uf, mun, zona, secao, hash_, nome):
    return f"{BASE}/arquivo-urna/{pleito}/dados/{uf}/{mun}/{zona}/{secao}/{hash_}/{nome}"


class Cliente:
    def __init__(self, max_por_segundo=40, timeout=20):
        self.intervalo = 1.0 / max_por_segundo
        self.timeout = timeout
        self.etags = {}
        self.cache = {}
        self._trava = threading.Lock()
        self._proximo = 0.0

    def _esperar_vez(self):
        with self._trava:
            agora = time.monotonic()
            espera = self._proximo - agora
            self._proximo = max(agora, self._proximo) + self.intervalo
        if espera > 0:
            time.sleep(espera)

    def get(self, url, tentativas=3):
        """Retorna (json, mudou). Com ETag, um 304 devolve o JSON em cache e mudou=False."""
        return self._baixar(url, tentativas, como_json=True)

    def get_bytes(self, url, tentativas=3):
        """Arquivo binário (ex.: boletim de urna), sem cache."""
        return self._baixar(url, tentativas, como_json=False)[0]

    def _baixar(self, url, tentativas, como_json):
        for tentativa in range(tentativas):
            self._esperar_vez()
            req = urllib.request.Request(url, headers={"User-Agent": "apuracao-2t/1.0"})
            if como_json and url in self.etags:
                req.add_header("If-None-Match", self.etags[url])
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    corpo = r.read()
                    if not como_json:
                        return corpo, True
                    dado = json.loads(corpo)
                    if r.headers.get("ETag"):
                        self.etags[url] = r.headers["ETag"]
                    self.cache[url] = dado
                    return dado, True
            except urllib.error.HTTPError as erro:
                if erro.code == 304:
                    return self.cache[url], False
                if erro.code == 404 or tentativa == tentativas - 1:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if tentativa == tentativas - 1:
                    raise
            time.sleep(1 + tentativa)


def _int(valor):
    return int(valor or 0)


def resumo(dado):
    """Achata o JSON de uma abrangência (país, UF ou município) nos números que o modelo usa."""
    cand = {}
    for agr in dado["carg"][0]["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                cand[c["n"]] = _int(c["vap"])
    s, e, v = dado["s"], dado["e"], dado["v"]
    return {
        "cand": cand,
        "secoes": _int(s["ts"]),
        "secoes_tot": _int(s["st"]),
        "eleitores": _int(e["te"]),
        "eleitores_tot": _int(e["est"]),
        "comparec": _int(e["c"]),
        "validos": _int(v["vv"]),
        "brancos": _int(v["vb"]),
        "nulos": _int(v["tvn"]),
        "final": dado.get("tf") == "s",
        "gerado": f"{dado.get('dg', '')} {dado.get('hg', '')}".strip(),
    }
