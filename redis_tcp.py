"""Cliente Redis mínimo (protocolo RESP sobre TCP), só com a biblioteca padrão.

Serve para REDIS_URL do tipo redis://usuario:senha@host:porta (ou rediss:// com TLS), como a
que a Vercel cria para o Redis Cloud. Abre uma conexão por comando, o que basta para um SET a
cada 30 segundos.
"""

import socket
import ssl
import urllib.parse


def _montar(partes):
    saida = [f"*{len(partes)}\r\n".encode()]
    for p in partes:
        b = p if isinstance(p, bytes) else str(p).encode()
        saida += [f"${len(b)}\r\n".encode(), b, b"\r\n"]
    return b"".join(saida)


def _ler(arquivo):
    linha = arquivo.readline()
    if not linha:
        raise ConnectionError("o Redis fechou a conexão")
    tipo, resto = linha[:1], linha[1:-2]
    if tipo == b"+":
        return resto.decode()
    if tipo == b"-":
        raise RuntimeError(f"Redis: {resto.decode()}")
    if tipo == b":":
        return int(resto)
    if tipo == b"$":
        n = int(resto)
        return None if n < 0 else arquivo.read(n + 2)[:-2]
    raise RuntimeError(f"resposta inesperada do Redis: {linha[:40]!r}")


class RedisTCP:
    def __init__(self, url, timeout=10):
        u = urllib.parse.urlparse(url)
        self.host, self.porta = u.hostname, u.port or 6379
        self.tls = u.scheme == "rediss"
        self.usuario = urllib.parse.unquote(u.username or "")
        self.senha = urllib.parse.unquote(u.password or "")
        self.timeout = timeout

    def comando(self, *partes):
        sock = socket.create_connection((self.host, self.porta), timeout=self.timeout)
        if self.tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        with sock, sock.makefile("rb") as arquivo:
            if self.senha:
                auth = ("AUTH", self.usuario, self.senha) if self.usuario and self.usuario != "default" else ("AUTH", self.senha)
                sock.sendall(_montar(auth))
                _ler(arquivo)
            sock.sendall(_montar(partes))
            return _ler(arquivo)
