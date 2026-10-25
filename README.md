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

## Rodar na nuvem (GitHub Actions)

O workflow `.github/workflows/apuracao.yml` roda o poller e o leitor de boletins do exterior
nas máquinas do GitHub, sem depender de nenhum computador ligado:

1. Cadastre a `REDIS_URL` como segredo do repositório (o valor sai do `.env.local`):

   ```bash
   python3 -c "import re;print(re.search(r'REDIS_URL=[\"\x27]?([^\"\x27\n]+)', open('.env.local').read()).group(1), end='')" | gh secret set REDIS_URL
   ```

2. Inicie a primeira execução: **Actions → Apuração ao vivo → Run workflow** (ou `gh workflow run apuracao.yml`).

Cada execução dura ~5h30. O agendamento a cada 10 minutos deixa a próxima na fila (as
intermediárias aparecem como canceladas, é normal) e ela retoma do estado salvo no Redis:
histórico dos gráficos, municípios já apurados e boletins do exterior já lidos. Depois do
meio-dia de 26/10 as execuções saem na hora; para desligar antes, `gh workflow disable apuracao.yml`.

Nunca rode o poller no seu computador ao mesmo tempo que o Actions: os dois gravariam no mesmo Redis.

## Rodar durante a apuração (no seu computador)

```bash
python3 poller.py         # projeção a cada 30s; envia ao Redis se houver .env.local
python3 exterior_bu.py    # boletins de urna do exterior, seção por seção
python3 servidor.py       # (opcional) a página local em http://127.0.0.1:8766
```

O exterior vota no horário local de cada país, então seus boletins saem antes da totalização
oficial, que começa quando a votação termina no Brasil (hoje, prorrogada até as 19h de Brasília). `exterior_bu.py` lê esses boletins (`bu.dat`, decodificado por
`bu.py`) e grava `data/exterior_bu.json`; o poller usa essa fonte na seção do exterior enquanto
ela cobrir mais eleitores que a totalização do TSE.

Ensaio (2º turno inventado sobre o 1º turno real, para testar a página; nunca vai para o site):

```bash
python3 poller.py --ensaio --minutos 10                # grava public/ensaio.json
python3 -m http.server 8765 --directory public         # e abra http://127.0.0.1:8765/?ensaio=1
```

(O `servidor.py` entrega só a página e o `live.json`, de propósito; para o ensaio use o servidor acima.)

## O modelo e a validação

O modelo em uso é a versão 2 (`modelo.py`), feita a partir da revisão em
`docs/revisao-metodologia.md`. A versão 1 fica congelada em `modelo_v1.py`, só para comparação.
A página explica o método na seção "Sobre este painel"; em resumo, a v2 acrescenta:

- um termo para o viés de composição dos municípios ainda parcialmente apurados, que é estimado
  junto com o resto e não vai para a previsão do que falta;
- uma covariável por eliminado com ≥ 1% dos válidos (em 2026: Cury, Renan Santos, Caiado);
- heterogeneidade entre municípios reestimada a cada leitura, sem piso artificial;
- desvios de região e UF, erro do que falta e prior do viés de composição calibrados em 2014 e
  2018, com 2022 fora do ajuste;
- a chance de vitória só é publicada com ≥ 10% do eleitorado apurado e dados de ≥ 20 estados.

Para refazer a validação:

```bash
# dados abertos do TSE (votação por seção e detalhe por zona), para cada ano:
#   https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/votacao_secao_ANO_BR.zip
#   https://cdn.tse.jus.br/estatistica/sead/odsele/detalhe_votacao_munzona/detalhe_votacao_munzona_ANO.zip
python3 preparar_eleicao.py 2022 votacao_secao_2022_BR.zip detalhe_votacao_munzona_2022.zip   # idem 2014 e 2018
python3 backtest.py              # v1 × v2 nas três eleições, 9 ordens de chegada, 8 pontos cada
python3 backtest.py --calibrar   # grade de parâmetros em 2014 e 2018
```

Resultado (192 pontos, sem o teste extremo): erro médio da projeção 0,50 p.p. (v1: 0,56), faixa
de 90% contém o resultado em 90% dos pontos (v1: 86%), nenhuma chance ≥ 95% para quem perdeu
(v1: 2). Em 2022, fora do ajuste, a v2 empata com a v1 (0,29 × 0,30 p.p.). O limite conhecido é a
comparação por município: com as zonas mais adversas chegando primeiro dentro de cada cidade, a
faixa de 90% contém o resultado em 54–62% dos pontos (v1: 46%).

Cada execução do Actions guarda o estado por município de cada ciclo como artefato
(`snapshots-<id>`): é a ordem real de chegada desta noite, para calibrar a próxima versão.

## Arquivos

- `tse.py`: URLs e cliente HTTP dos JSONs públicos do TSE (ETag, limite de requisições).
- `baixar_base.py`: baixa o 1º turno de cada município (`data/base_1t_2026.json`, já incluído).
- `modelo.py`: projeção, versão 2 (virada + comparecimento + Monte Carlo com erro por UF e nacional).
- `modelo_v1.py`: a versão 1, congelada, usada só no backtest para comparação.
- `poller.py`: loop ao vivo (ou ensaio) que roda o modelo, grava `public/live.json` e envia ao Redis.
- `redis_tcp.py`: cliente Redis mínimo (só biblioteca padrão) para a `REDIS_URL`.
- `exterior_bu.py` / `bu.py`: leitura dos boletins de urna do exterior.
- `public/index.html`: a página; lê `live.json` a cada 30s.
- `api/live.js` / `vercel.json`: a função e a configuração da Vercel.
- `servidor.py`: servidor local que entrega só a página e o JSON.
- `preparar_eleicao.py` / `backtest.py`: validação com os 2º turnos de 2014, 2018 e 2022.
- `armazem.py`: acesso ao Redis (publicação e estado que sobrevive a um reinício).
- `docs/revisao-metodologia.md`: a revisão crítica da versão 1 que guiou a versão 2.
