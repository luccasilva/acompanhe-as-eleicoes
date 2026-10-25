# Acompanhe as eleições · 2º turno 2026

Painel que acompanha ao vivo a apuração do 2º turno para presidente (Lula × Flávio Bolsonaro)
e projeta o resultado final a partir da virada de cada município sobre o 1º turno. Usa só os
arquivos públicos de resultados do TSE.

## Como as peças se ligam

```
TSE (JSON públicos) ─► poller.py ─────────┐
TSE (boletins do exterior) ─► exterior_bu.py ─┘─► Redis ─► api/live.js (Vercel) ─► página
```

A Vercel só serve a página (`public/`) e a função `api/live.js`, que lê do Redis o último
`live.json` e deixa a CDN guardar por 10 segundos. O cálculo roda no `poller.py`, que precisa
ficar ligado durante a apuração e envia o resultado ao Redis a cada 30 segundos.

## Publicar na Vercel (uma vez)

1. Na Vercel: **Add New → Project** e importe `luccasilva/acompanhe-as-eleicoes`.
   Framework Preset: **Other**. Não mude build nem output: o `vercel.json` já aponta para `public/`.
2. No projeto, aba **Storage → Create Database → Redis** e conecte ao projeto. Isso cria a
   variável `REDIS_URL`. (Também funciona com **Upstash for Redis**, que cria
   `KV_REST_API_URL` e `KV_REST_API_TOKEN`; o código aceita os dois.)
3. **Deployments → ⋯ → Redeploy**, para a função enxergar as variáveis.
4. Copie a variável para um arquivo `.env.local` na pasta do projeto (ele fica fora do Git):

   ```
   REDIS_URL="redis://default:...@....db.redis.io:12345"
   ```

5. Reinicie o poller. A cada ciclo ele passa a imprimir "enviado ao site".

Antes do primeiro envio, `/live.json` responde 503 ("ainda sem dados") e a página mostra um aviso.

## Rodar durante a apuração

```bash
python3 poller.py         # projeção a cada 30s; envia ao Redis se houver .env.local
python3 exterior_bu.py    # boletins de urna do exterior, seção por seção
python3 servidor.py       # (opcional) a página local em http://127.0.0.1:8766
```

O exterior vota no horário local de cada país, então seus boletins saem antes da totalização
oficial (17h de Brasília). `exterior_bu.py` lê esses boletins (`bu.dat`, decodificado por
`bu.py`) e grava `data/exterior_bu.json`; o poller usa essa fonte na seção do exterior enquanto
ela cobrir mais eleitores que a totalização do TSE.

Ensaio (2º turno inventado sobre o 1º turno real, para testar a página; nunca vai para o site):

```bash
python3 poller.py --ensaio --minutos 10    # grava public/ensaio.json
python3 servidor.py                         # e abra http://127.0.0.1:8766/?ensaio=1 (só local)
```

## Arquivos

- `tse.py`: URLs e cliente HTTP dos JSONs públicos do TSE (ETag, limite de requisições).
- `baixar_base.py`: baixa o 1º turno de cada município (`data/base_1t_2026.json`, já incluído).
- `modelo.py`: projeção (virada + comparecimento + Monte Carlo com erro por UF e nacional).
- `poller.py`: loop ao vivo (ou ensaio) que roda o modelo, grava `public/live.json` e envia ao Redis.
- `redis_tcp.py`: cliente Redis mínimo (só biblioteca padrão) para a `REDIS_URL`.
- `exterior_bu.py` / `bu.py`: leitura dos boletins de urna do exterior.
- `public/index.html`: a página; lê `live.json` a cada 30s.
- `api/live.js` / `vercel.json`: a função e a configuração da Vercel.
- `servidor.py`: servidor local que entrega só a página e o JSON.
- `backtest_2022.py` / `preparar_2022.py`: validação do modelo com o 1º → 2º turno de 2022.
