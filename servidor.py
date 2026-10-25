"""Servidor da página para o público: entrega só a página e o live.json.

Diferente do `python3 -m http.server`, não lista diretórios nem serve outros arquivos,
comprime as respostas (o live.json cai de ~36 KB para ~8 KB) e define cache curto, para
aguentar muitos visitantes atualizando a cada 30 segundos.

Uso: python3 servidor.py [--porta 8766]
"""

import argparse
import gzip
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUBLICO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
ROTAS = {
    "/": ("index.html", "text/html; charset=utf-8", "public, max-age=60"),
    "/index.html": ("index.html", "text/html; charset=utf-8", "public, max-age=60"),
    "/live.json": ("live.json", "application/json; charset=utf-8", "public, max-age=10"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "apuracao"
    sys_version = ""

    def do_GET(self):
        self._responder(com_corpo=True)

    def do_HEAD(self):
        self._responder(com_corpo=False)

    def _responder(self, com_corpo):
        rota = ROTAS.get(self.path.split("?", 1)[0])
        if not rota:
            self.send_error(404)
            return
        arquivo, tipo, cache = rota
        try:
            with open(os.path.join(PUBLICO, arquivo), "rb") as f:
                corpo = f.read()
        except OSError:
            self.send_error(503, "ainda sem dados")
            return
        comprimir = "gzip" in self.headers.get("Accept-Encoding", "")
        if comprimir:
            corpo = gzip.compress(corpo, 6)
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if comprimir:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        if com_corpo:
            self.wfile.write(corpo)

    def log_message(self, formato, *args):
        pass  # sem log por requisição; com muito público isso vira ruído


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8766)
    args = ap.parse_args()
    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Handler)
    servidor.daemon_threads = True
    print(f"servindo public/ (só página e live.json) em http://127.0.0.1:{args.porta}", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
