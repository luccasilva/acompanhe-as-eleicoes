// Entrega o live.json mais recente, que o poller grava no Redis (Upstash) a cada ciclo de 30s.
// O cache curto da CDN da Vercel faz o Redis ser lido no máximo a cada ~10s por região,
// não importa quantos visitantes estejam com a página aberta.

// A integração da Upstash na Vercel cria KV_REST_API_URL/KV_REST_API_TOKEN, mas o prefixo
// pode ser trocado na criação; por isso também vale qualquer *_REST_API_URL.
const env = process.env;
const achar = (sufixo, sem) => env[Object.keys(env).find((k) => k.endsWith(sufixo) && !(sem && k.includes(sem)))];
const URL_REDIS = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL || achar("_REST_API_URL");
const TOKEN =
  env.KV_REST_API_READ_ONLY_TOKEN || env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN ||
  achar("_REST_API_READ_ONLY_TOKEN") || achar("_REST_API_TOKEN", "READ_ONLY");

module.exports = async (req, res) => {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  if (!URL_REDIS || !TOKEN) {
    res.statusCode = 503;
    res.end(JSON.stringify({ erro: "Redis não configurado no projeto da Vercel" }));
    return;
  }
  try {
    const r = await fetch(`${URL_REDIS}/get/live`, { headers: { Authorization: `Bearer ${TOKEN}` } });
    const { result } = await r.json();
    if (!result) {
      res.statusCode = 503;
      res.setHeader("Cache-Control", "public, s-maxage=5");
      res.end(JSON.stringify({ erro: "ainda sem dados" }));
      return;
    }
    res.statusCode = 200;
    res.setHeader("Cache-Control", "public, s-maxage=10, stale-while-revalidate=30");
    res.end(result);
  } catch (erro) {
    res.statusCode = 502;
    res.end(JSON.stringify({ erro: "falha ao ler o Redis" }));
  }
};
