// Contador de "pessoas acompanhando agora".
//
// Cada aba aberta envia um sinal com um id aleatório gerado no próprio navegador (sem cookie e
// sem dado pessoal). O Redis guarda só id → hora do último sinal, num conjunto que se limpa
// sozinho. Para uma pessoa sozinha não inflar o número, cada IP conta no máximo MAX_POR_IP abas
// ativas; o IP nunca é guardado, só um hash com sal secreto, por 3 minutos.
//
// A carga é limitada: o intervalo entre sinais cresce com a audiência, para que o total fique
// perto de CARGA_MAX sinais por segundo. Este contador não toca nos dados da apuração.

const crypto = require("crypto");
const { conectar } = require("./_redis");

const URL_REDIS = process.env.REDIS_URL || process.env.KV_URL;
const ID_VALIDO = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_POR_IP = 30; // abas ativas por IP (operadoras de celular põem muita gente atrás de um IP)
const JANELA_IP_MS = 180_000;
const CARGA_MAX = 10; // sinais por segundo, somando todo mundo
const INTERVALO_MIN = 60; // segundos
const TAMANHO_MAX = 100_000; // teto do conjunto, para proteger a memória do Redis
const SAL = crypto.createHash("sha256").update(`online:${URL_REDIS || ""}`).digest();

function responder(res, status, corpo) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(corpo));
}

module.exports = async (req, res) => {
  if (req.method !== "POST") return responder(res, 405, { erro: "use POST" });
  if (!URL_REDIS) return responder(res, 503, { erro: "contador desligado" });
  const id = new URL(req.url, "http://x").searchParams.get("id") || "";
  if (!ID_VALIDO.test(id)) return responder(res, 400, { erro: "id inválido" });

  const ip = String(req.headers["x-real-ip"] || req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  const chaveIp = "online:ip:" + crypto.createHmac("sha256", SAL).update(ip).digest("hex").slice(0, 20);
  const agora = Date.now();
  let redis;
  try {
    redis = conectar(URL_REDIS);
    const [total, , , , abasDoIp] = await redis.comandos([
      ["ZCARD", "online"],
      ["ZADD", chaveIp, agora, id],
      ["ZREMRANGEBYSCORE", chaveIp, 0, agora - JANELA_IP_MS],
      ["EXPIRE", chaveIp, Math.ceil(JANELA_IP_MS / 1000)],
      ["ZCARD", chaveIp],
    ]);
    const intervalo = Math.max(INTERVALO_MIN, Math.ceil(total / CARGA_MAX));
    const janelaMs = Math.round(2.5 * intervalo * 1000); // quem não deu sinal nesse tempo saiu
    const conta = abasDoIp <= MAX_POR_IP && total < TAMANHO_MAX;
    const lista = [["ZREMRANGEBYSCORE", "online", 0, agora - janelaMs]];
    if (conta) lista.push(["ZADD", "online", agora, id]);
    lista.push(["ZCARD", "online"]);
    const respostas = await redis.comandos(lista);
    responder(res, 200, { online: respostas[respostas.length - 1], intervalo });
  } catch (erro) {
    responder(res, 503, { erro: "contador indisponível" });
  } finally {
    if (redis) redis.fechar();
  }
};
