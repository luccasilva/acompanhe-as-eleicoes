// Cliente Redis mínimo para as funções da Vercel: protocolo RESP sobre TCP (ou TLS com
// rediss://), sem dependências, com vários comandos por conexão.
// Arquivos de api/ que começam com "_" não viram funções na Vercel.

const net = require("net");
const tls = require("tls");

function montar(partes) {
  const pedacos = [Buffer.from(`*${partes.length}\r\n`)];
  for (const p of partes) {
    const b = Buffer.from(String(p));
    pedacos.push(Buffer.from(`$${b.length}\r\n`), b, Buffer.from("\r\n"));
  }
  return Buffer.concat(pedacos);
}

// Lê uma resposta RESP do início do buffer; null se ela ainda não chegou inteira.
function lerResposta(buf) {
  const fimLinha = buf.indexOf("\r\n");
  if (fimLinha < 0) return null;
  const tipo = String.fromCharCode(buf[0]);
  const linha = buf.toString("utf8", 1, fimLinha);
  if (tipo === "+") return { valor: linha, fim: fimLinha + 2 };
  if (tipo === ":") return { valor: Number(linha), fim: fimLinha + 2 };
  if (tipo === "-") return { erro: linha, fim: fimLinha + 2 };
  if (tipo === "$") {
    const n = Number(linha);
    if (n < 0) return { valor: null, fim: fimLinha + 2 };
    const ini = fimLinha + 2;
    if (buf.length < ini + n + 2) return null;
    return { valor: buf.toString("utf8", ini, ini + n), fim: ini + n + 2 };
  }
  return { erro: "resposta inesperada do Redis", fim: buf.length };
}

function conectar(url, timeoutMs = 4000) {
  const u = new URL(url);
  const seguro = u.protocol === "rediss:";
  const opcoes = { host: u.hostname, port: Number(u.port || 6379) };
  const sock = seguro ? tls.connect({ ...opcoes, servername: u.hostname }) : net.connect(opcoes);
  const usuario = decodeURIComponent(u.username || "");
  const senha = decodeURIComponent(u.password || "");
  let auth = senha ? (usuario && usuario !== "default" ? ["AUTH", usuario, senha] : ["AUTH", senha]) : null;
  let buf = Buffer.alloc(0);
  let pendente = null;

  const falhar = (erro) => {
    if (pendente) {
      const p = pendente;
      pendente = null;
      p.reject(erro);
    }
  };
  const pronto = new Promise((ok, erro) => {
    sock.once(seguro ? "secureConnect" : "connect", ok);
    sock.once("error", erro);
  });
  sock.setTimeout(timeoutMs, () => sock.destroy(new Error("tempo esgotado no Redis")));
  sock.on("error", falhar);
  sock.on("close", () => falhar(new Error("o Redis fechou a conexão")));
  sock.on("data", (pedaco) => {
    buf = Buffer.concat([buf, pedaco]);
    let r;
    while (pendente && (r = lerResposta(buf))) {
      buf = buf.subarray(r.fim);
      pendente.respostas.push(r);
      if (pendente.respostas.length === pendente.n) {
        const p = pendente;
        pendente = null;
        p.resolve(p.respostas);
      }
    }
  });

  return {
    // Envia uma lista de comandos de uma vez e devolve os valores, na mesma ordem.
    async comandos(lista) {
      await pronto;
      const enviar = auth ? [auth, ...lista] : lista;
      const respostas = await new Promise((resolve, reject) => {
        pendente = { n: enviar.length, respostas: [], resolve, reject };
        sock.write(Buffer.concat(enviar.map(montar)));
      });
      if (auth) {
        auth = null;
        const r = respostas.shift();
        if (r.erro) throw new Error(r.erro);
      }
      const erro = respostas.find((r) => r.erro);
      if (erro) throw new Error(erro.erro);
      return respostas.map((r) => r.valor);
    },
    fechar() {
      sock.end();
    },
  };
}

module.exports = { conectar };
