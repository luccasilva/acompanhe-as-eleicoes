// Entrega o live.json mais recente, que o poller grava no Redis a cada ciclo de 30s.
// O cache curto da CDN da Vercel faz o Redis ser lido no máximo a cada ~10s por região,
// não importa quantos visitantes estejam com a página aberta.
//
// Aceita REDIS_URL (protocolo Redis, como o Redis Cloud que a Vercel cria) ou a API REST
// da Upstash (KV_REST_API_URL + token; o prefixo KV_ pode ter sido trocado na criação).

const net = require("net");
const tls = require("tls");

const env = process.env;
const achar = (sufixo, sem) => env[Object.keys(env).find((k) => k.endsWith(sufixo) && !(sem && k.includes(sem)))];
const URL_REST = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL || achar("_REST_API_URL");
const TOKEN_REST =
  env.KV_REST_API_READ_ONLY_TOKEN || env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN ||
  achar("_REST_API_READ_ONLY_TOKEN") || achar("_REST_API_TOKEN", "READ_ONLY");
const URL_REDIS = env.REDIS_URL || env.KV_URL;

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
  if (tipo === "+" || tipo === ":") return { valor: linha, fim: fimLinha + 2 };
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

function getTCP(url, chave) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const seguro = u.protocol === "rediss:";
    const opcoes = { host: u.hostname, port: Number(u.port || 6379) };
    const sock = seguro ? tls.connect({ ...opcoes, servername: u.hostname }) : net.connect(opcoes);
    const usuario = decodeURIComponent(u.username || "");
    const senha = decodeURIComponent(u.password || "");
    const fila = [];
    if (senha) fila.push(usuario && usuario !== "default" ? ["AUTH", usuario, senha] : ["AUTH", senha]);
    fila.push(["GET", chave]);
    let buf = Buffer.alloc(0);
    let lidas = 0;
    sock.setTimeout(8000, () => sock.destroy(new Error("tempo esgotado no Redis")));
    sock.on("error", reject);
    sock.on(seguro ? "secureConnect" : "connect", () => sock.write(Buffer.concat(fila.map(montar))));
    sock.on("data", (pedaco) => {
      buf = Buffer.concat([buf, pedaco]);
      let r;
      while ((r = lerResposta(buf))) {
        buf = buf.subarray(r.fim);
        if (r.erro) return sock.destroy(new Error(r.erro));
        if (++lidas === fila.length) {
          sock.end();
          return resolve(r.valor);
        }
      }
    });
  });
}

async function getREST(chave) {
  const r = await fetch(`${URL_REST}/get/${chave}`, { headers: { Authorization: `Bearer ${TOKEN_REST}` } });
  return (await r.json()).result;
}

module.exports = async (req, res) => {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  if (!(URL_REST && TOKEN_REST) && !URL_REDIS) {
    res.statusCode = 503;
    res.end(JSON.stringify({ erro: "Redis não configurado no projeto da Vercel" }));
    return;
  }
  try {
    const valor = URL_REST && TOKEN_REST ? await getREST("live") : await getTCP(URL_REDIS, "live");
    if (!valor) {
      res.statusCode = 503;
      res.setHeader("Cache-Control", "public, s-maxage=5");
      res.end(JSON.stringify({ erro: "ainda sem dados" }));
      return;
    }
    res.statusCode = 200;
    res.setHeader("Cache-Control", "public, s-maxage=10, stale-while-revalidate=30");
    res.end(valor);
  } catch (erro) {
    res.statusCode = 502;
    res.end(JSON.stringify({ erro: "falha ao ler o Redis" }));
  }
};
