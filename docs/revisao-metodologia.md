# Revisão da metodologia de projeção do 2º turno

Diagnóstico crítico do modelo atual (`modelo.py`, commit `9da35e0`) e spec de uma versão 2.
Escrito em 25/10/2026, dia do 2º turno, sem nenhuma alteração no código.

---

## 0. Resumo

O modelo é bem pensado. Ancorar a projeção na virada sobre o 1º turno, encolher por UF e
região e simular erros correlacionados é a estrutura certa, e no backtest de 2022 ele erra em
média 0,18 p.p. contra 5,3 p.p. da extrapolação ingênua. Os problemas não estão na ideia
central. Estão em quatro lugares:

1. **A linha de base é o município inteiro, não as seções já apuradas.** Num município
   parcialmente apurado, o modelo compara o parcial do 2º turno com o 1º turno da cidade toda.
   Se as zonas que chegam primeiro votam diferente da média da cidade, isso vira "virada"
   falsa. Em 2022, nas 42 cidades com 4 ou mais zonas, essa comparação erra a virada da cidade
   em 2,4 p.p. (RMSE) com 20% apurado, contra 0,3 p.p. comparando seção com seção. Quando as
   zonas mais anti-Lula chegam primeiro dentro de cada cidade, o modelo projeta Lula 1,6 p.p.
   abaixo do real e dá a ele 25% de chance numa eleição que ele ganhou por 1,8 p.p.
2. **A incerteza está no lugar errado.** Nos cenários benignos de 2022, a faixa declarada é de
   3 a 25 vezes maior que o erro real, e a "chance de vitória" fica em 60–88% com o resultado
   já praticamente definido. Já nos cenários em que a ordem de chegada se correlaciona com o
   voto, o modelo fica confiante no lado errado: num teste extremo, chega a 0,1% de chance para
   o candidato que venceu. As peças de incerteza (σ_zona, erro "tardio", priors de UF) foram
   fixadas à mão e não protegem contra o erro que realmente ocorre.
3. **O backtest tem n = 1, e a "ordem real 2022" não é a ordem real.** A ordem é reconstruída
   a partir da hora em que cada zona *terminou*, que é dominada pela última seção a chegar. Na
   prática o cenário vira uma ordem quase aleatória: o parcial ingênuo fica parado em ~50,7%
   do 10% ao 90%. Com uma eleição só, não dá para calibrar nem saber se os parâmetros foram
   ajustados olhando para o próprio teste.
4. **O modelo da virada é simples demais para 2026.** Os eliminados entram como um bloco único
   ("outros", 7,8%), embora sejam três candidatos de 2–3% cada, com geografias diferentes. A
   heterogeneidade entre municípios é tratada como igual para todos, quando em 2022 ela era
   três vezes maior nas cidades pequenas. E não entra nenhuma informação externa: nem
   pesquisa, nem o 2º turno de 2022.

A prioridade da spec (seção 6) é: **(1) montar um backtest honesto com várias eleições e a
ordem de chegada real, (2) trocar a linha de base para seção, (3) recalibrar a incerteza
nesse backtest, (4) só então enriquecer o modelo da virada.** Sem o item 1, os outros não
podem ser medidos.

**Para hoje à noite:** não mexa no código. A seção 5 traz um guia de leitura do painel com
essas limitações em mente.

---

## 1. Como o modelo funciona hoje

Para fixar o vocabulário (detalhes em `modelo.py` e na seção "Detalhes técnicos" da página):

| Peça | O que faz | Onde |
|---|---|---|
| Virada | `y_m = A2/(A2+B2) − A1/(A1+B1)` por município apurado, com ≥ 20 votos | `modelo.py:112` |
| Regressão | Modelo misto: intercepto, `p1`, `outros`, `outros×p1`, `log10(eleitores)`, `capital`, efeito de região, efeito de UF. Priors fixas; σ_mun reestimado | `modelo.py:110-143` |
| Ruído por observação | `σ_mun² + 0,25/n + σ_zona²(1 − f)`, com σ_zona = 2 p.p. | `modelo.py:133` |
| Município parcial | O que falta recebe `λ·y_m + (1−λ)·ŷ_m` (BLUP) | `modelo.py:145-153` |
| Comparecimento | Razão de válidos por eleitor 2T/1T, encolhida município → UF → região → país | `modelo.py:92-102` |
| Monte Carlo | θ ~ N(θ̂, Σ) + ruído municipal por UF + "tardio" N(0; 1 p.p.) por UF e nacional + choque de comparecimento | `modelo.py:163-177` |

---

## 2. O que está bom (e deve ficar)

- **A âncora no 1º turno.** É o que tira quase toda a variação geográfica do problema. No
  backtest, o erro médio cai de 5,3 p.p. (ingênuo) para 0,18 p.p.
- **O termo dos eliminados foi deduzido, não chutado.** Virada ≈ `o·(a − p)` justifica
  `outros` e `outros×p1` como covariáveis. É a forma linear de um modelo de transferência.
- **Encolhimento hierárquico e BLUP para municípios parciais.** É a ideia certa; o problema
  está nos números que entram nela (seções 3.1 e 3.4).
- **Erros correlacionados por UF e nacional no Monte Carlo.** Sem isso, a faixa sairia
  absurdamente estreita.
- **Existe um backtest, com cenários adversos por região.** A maioria dos painéis desse tipo
  não tem nenhum.
- **A página não mostra probabilidade antes do primeiro dado.**
- **O código é pequeno, só numpy, fácil de auditar.**

---

## 3. Críticas, da mais grave para a menos grave

Cada item traz o problema, a evidência, por que importa e o que fazer. Os números vêm de
scripts que rodam o `modelo.py` sem alterações sobre os dados de 2022 por seção
(`data/secoes_2022.npz`). Veja o apêndice.

### 3.1 Linha de base municipal: viés de composição dentro da cidade

**O problema.** `y_m` compara o parcial do 2º turno com o 1º turno **do município inteiro**
(`modelo.py:112`). O TSE divulga por município sem dizer quais seções já entraram. Se as
seções apuradas não são uma amostra representativa da cidade (centro antes da periferia,
urbano antes do rural, zonas que transmitem mais rápido), a diferença de composição vira
virada.

**Evidência 1: o tamanho do ruído.** Nas 42 cidades de 2022 com 4 ou mais zonas (29% do
eleitorado), incluindo zonas inteiras até chegar à fração indicada, medimos o erro da virada
parcial como estimativa da virada final da cidade:

| Ordem das zonas na cidade | Apurado | Erro vs 1T do município (o que o modelo usa) | Erro vs 1T das mesmas seções |
|---|---|---|---|
| Aleatória | 20% | viés +1,11 p.p., RMSE **2,44 p.p.** | viés −0,07 p.p., RMSE **0,33 p.p.** |
| Aleatória | 50% | viés −0,12 p.p., RMSE 1,09 p.p. | viés +0,04 p.p., RMSE 0,17 p.p. |
| Mais anti-Lula primeiro | 20% | viés **−5,38 p.p.**, RMSE 6,24 p.p. | viés +0,70 p.p., RMSE 0,91 p.p. |
| Mais anti-Lula primeiro | 50% | viés −3,20 p.p., RMSE 3,83 p.p. | viés +0,37 p.p., RMSE 0,50 p.p. |

Comparar seção com seção reduz o ruído cerca de 7 vezes mesmo com ordem aleatória, e quase
elimina o viés com ordem adversa.

**Evidência 2: o efeito no modelo.** Municípios chegando em ordem aleatória; dentro de cada
um, as zonas com menor fatia de Lula no 1º turno primeiro. Verdade: 50,90%.

| Apurado | Ingênuo | Projeção [90%] | P(Lula) | Erro |
|---|---|---|---|---|
| 5% | 53,34% | 49,27% [47,49; 50,99] | **24,9%** | −1,63 p.p. |
| 10% | 52,26% | 49,45% [47,79; 51,01] | 29,3% | −1,46 p.p. |
| 20% | 52,14% | 49,94% [48,49; 51,33] | 47,7% | −0,96 p.p. |
| 30% | 51,67% | 50,17% [48,90; 51,41] | 59,0% | −0,73 p.p. |
| 50% | 51,79% | 50,50% [49,54; 51,40] | 80,7% | −0,40 p.p. |

Com a mesma chegada dos municípios e as zonas em ordem aleatória, o erro fica entre −0,15 e
+0,22 p.p. em todos os pontos. A diferença inteira vem da composição.

Isso **é um teste de sensibilidade, não uma prova de que acontece**. Não sabemos se, numa
noite real, as zonas que chegam primeiro em cada cidade votam diferente da média. O ponto é
que o modelo não tem defesa nenhuma se acontecer, e em 2026 Lula precisa de uma virada de
+1,0 p.p.: um viés de 1,5 p.p. troca o vencedor projetado.

**Efeito colateral: o modelo quase ignora o próprio parcial das cidades grandes.** Como
σ_zona = 2 p.p. (coerente com o RMSE de 2,4 p.p. acima) e σ_mun bate no piso de 0,5 p.p. na
maior parte das leituras, o peso do parcial da cidade no que falta dela fica em
`λ = σ_mun²/(σ_mun² + σ_zona²(1−f))`:

| Fração apurada da cidade | λ hoje | λ com base por seção (estimativa) |
|---|---|---|
| 20% | ~0,07 | ~0,7 |
| 50% | ~0,11 | ~0,9 |
| 90% | ~0,38 | ~1 |

Ou seja, com metade de São Paulo apurada, a projeção dos milhões de votos que faltam na
capital vem quase toda da regressão, puxada pelos outros 644 municípios paulistas, e só 11%
do parcial da própria capital. Na regressão, São Paulo pela metade pesa
`1/(0,005² + 0,02²·0,5) ≈ 4.400`, menos que um município de 2 mil votos já fechado (≈ 6.700).

**O que fazer.** Ver spec, fase 2: linha de base por seção a partir dos boletins de urna. Se
não der para ler boletins, a alternativa conservadora é tratar parte do erro de composição
como **comum entre municípios** (um componente correlacionado), e não como ruído independente.

### 3.2 A incerteza está no lugar errado

**Evidência 1: largo demais quando a ordem é benigna.** Sorteamos 12 ordens diferentes por
cenário e comparamos o erro real da mediana com o desvio-padrão que o modelo declara
(`(p95 − p05)/3,29`):

| Cenário | Apurado | DP real do erro | DP declarado | Razão | P(Lula) médio |
|---|---|---|---|---|---|
| Aleatório por zona | 5% | 0,25 p.p. | 1,02 p.p. | 0,24 | 79% |
| Aleatório por zona | 30% | 0,09 p.p. | 0,75 p.p. | 0,12 | 88% |
| Pequenos primeiro | 10% | 0,09 p.p. | 2,53 p.p. | **0,04** | 63% |
| Pequenos primeiro | 30% | 0,23 p.p. | 0,79 p.p. | 0,29 | 83% |
| Sul/Sudeste primeiro | 10% | 0,42 p.p. | 1,11 p.p. | 0,37 | 68% |
| Sul/Sudeste primeiro | 30% | 0,30 p.p. | 0,88 p.p. | 0,35 | 76% |

No backtest salvo, a cobertura do intervalo de 90% é **45 de 45** pontos, com meia-largura
média 8,8 vezes maior que o erro médio. Uma faixa de 90% que nunca erra está larga demais,
ou o teste é fácil demais (seção 3.3). Com 5% apurado em ordem aleatória, a margem de 1,8 p.p.
de 2022 já era conhecível com erro de ~0,25 p.p., e o painel mostraria algo como 79%.

Ressalva importante: a variação entre ordens sorteadas mede só uma fonte de incerteza (qual
pedaço chegou primeiro). A outra (o que falta ser sistematicamente diferente numa eleição que
não é 2022) não dá para medir com uma eleição. Por isso a conclusão não é "estreite a faixa",
e sim "calibre a faixa num conjunto de eleições".

**Evidência 2: estreito demais quando a ordem se correlaciona com o voto.** Além do caso da
seção 3.1, dois testes deliberadamente extremos:

- **Zonas do país inteiro ordenadas pela fatia do Lula no 1º turno** (as mais anti-Lula
  primeiro): o intervalo de 90% **não contém o resultado** do 10% ao 70% apurado, com erro de
  −1,9 a −0,5 p.p. e P(Lula) entre 17% e 30% até a metade da apuração.
- **Cidades grandes primeiro e, nelas, zonas anti-Lula primeiro:** com 10% e 20% apurados, a
  projeção é 44,9% e 46,0% (erro de −6,0 e −4,9 p.p.) e **P(Lula) = 0,1% e 0,0%**. O modelo só
  se recupera depois de 30–50%.

Ninguém espera um cenário desses numa noite real. Mas eles mostram que a incerteza do modelo
não tem nenhum termo para "o pedaço apurado é diferente do resto de um jeito que as
covariáveis não explicam". O erro "tardio" de 1 p.p. foi pensado para isso, mas é fixo, não
depende do quanto o apurado difere do resto e não foi calibrado com nada.

**Evidência 3: os componentes fixos são maiores do que 2022 mostra.** Em 2022, o desvio de
cada UF em relação à virada nacional teve DP de **0,46 p.p.** (máximo 1,23 p.p., no DF), e
dentro da região 0,44 p.p. O modelo assume a priori σ_região = 1,5 p.p. **e** σ_UF = 1,5 p.p.
(≈ 2,1 p.p. combinados para uma UF sem dado), mais 1 p.p. tardio por UF e 1 p.p. nacional.

**O que fazer.** Fase 4: estimar os componentes de variância em vez de fixá-los, fazer o erro
"tardio" crescer com a distância entre o apurado e o que falta, e calibrar tudo no backtest
com várias eleições. A meta é que o intervalo de 90% cubra ~90% em eleições que ficaram fora
do ajuste.

### 3.3 O backtest não sustenta as conclusões

**Problema 1: n = 1.** Todo o teste é uma eleição (2022), com os mesmos candidatos nos dois
turnos e uma virada pequena e homogênea. Os 45 pontos de checagem são muito correlacionados:
são a mesma eleição vista em ordens diferentes. Isso não basta para calibrar uma
probabilidade nem para dizer que a faixa de 90% é uma faixa de 90%.

**Problema 2: possível ajuste dentro da amostra.** `backtest_2022.py` aceita `--params`, o
que sugere que os parâmetros foram testados nele. Se algum valor de `PARAMS` foi escolhido
olhando para esse backtest, o desempenho reportado é dentro da amostra. Não dá para saber
pelo código. Vale registrar quais foram ajustados e como.

**Problema 3: a "ordem real 2022" é quase aleatória.** O cenário usa `u × fim_zona`
(`backtest_2022.py:57-59`): cada seção chega num instante uniforme entre 0 e a hora em que a
zona **terminou**. Essa hora é dominada pela seção mais atrasada da zona, então diz pouco
sobre quando a maior parte chegou. Resultado, o parcial ingênuo nesse cenário:

| Apurado | 1% | 5% | 10% | 20% | 30% | 50% | 70% | 90% |
|---|---|---|---|---|---|---|---|---|
| "Ordem real" do backtest | 51,80% | 50,96% | 50,76% | 50,71% | 50,73% | 50,67% | 50,71% | 50,80% |
| Zona inteira chega no seu `fim_zona` | 55,67% | 52,02% | 50,62% | 50,06% | 49,96% | 50,17% | 50,27% | 50,86% |

A primeira linha é praticamente plana, o mesmo comportamento do cenário "aleatório por zona".
Não reproduz a noite de 2022, em que a cobertura da época registrou Bolsonaro à frente no
parcial nacional durante boa parte da apuração (vale confirmar com snapshots arquivados).
Nenhuma variação simples do `fim_zona` reproduz isso: a informação de ordem não está nesse
arquivo.

**Problema 4: o que o backtest não mede.** Ele mede só a mediana nacional e a cobertura do
intervalo. Não mede a probabilidade (Brier, log-loss), as projeções por UF, nem o erro mais
importante para quem lê o painel: **apontar com alta confiança o candidato que perde**.

**O que fazer.** Fase 1. É pré-requisito de todo o resto.

### 3.4 A heterogeneidade não é igual para todos os municípios

**Evidência.** Resíduo de um ajuste com as mesmas covariáveis e efeito fixo de UF, 2022:

| Eleitores | Municípios | DP do resíduo | Peso nos votos |
|---|---|---|---|
| < 5 mil | 1.607 | **1,52 p.p.** | 3,3% |
| 5–10 mil | 1.478 | 0,82 p.p. | 6,7% |
| 10–20 mil | 1.284 | 0,70 p.p. | 11,4% |
| 20–50 mil | 859 | 0,61 p.p. | 16,2% |
| 50–100 mil | 266 | 0,55 p.p. | 11,9% |
| 100–300 mil | 156 | 0,58 p.p. | 17,2% |
| 300 mil–1 mi | 46 | 0,56 p.p. | 14,1% |
| > 1 mi | 12 | 0,52 p.p. | 19,1% |

O modelo usa um único σ_mun, estimado por média **não ponderada** dos resíduos
(`modelo.py:143`, `weights=w*0+1`). Isso tem três efeitos:

- a regressão é dominada pelos municípios pequenos (54% das observações, 10% dos votos), justo
  os mais ruidosos e menos parecidos com as cidades que decidem a eleição;
- a incerteza idiossincrática das cidades grandes fica superestimada e a das pequenas,
  subestimada;
- combinado com σ_zona, o σ_mun estimado bate no piso artificial de 0,5 p.p. na maioria das
  leituras dos testes, e é isso que derruba o λ da seção 3.1.

O valor inicial `sigma_mun_inicial = 3 p.p.`, usado com menos de 30 municípios, é três vezes o
DP não ponderado de 2022 (1,0 p.p.) e cinco vezes o ponderado (0,6 p.p.).

**O que fazer.** Fase 4: σ_mun como função do porte, estimado por REML, sem piso.

### 3.5 Os eliminados num bloco só, e uma virada sem limites

**O problema.** A covariável `outros` soma todos os eliminados com o mesmo coeficiente de
transferência. Em 2026 eles são três candidatos relevantes, com bases diferentes:

| Nº | % dos válidos (1T) | Onde é forte | Variância entre municípios que fica dentro da UF | Correlação com p1 |
|---|---|---|---|---|
| 70 | 2,89% | AP, SP, AM, MG (3,4–4,1%) | 58% | −0,11 |
| 14 | 2,24% | SP, SC, PR, DF (2,6–3,2%) | 45% | −0,31 |
| 55 | 2,18% | **GO (12,3%)**, DF (4,8%) | 12% | −0,26 |
| 30 | 0,27% | exterior, MG | 38% | −0,16 |

(Os nomes estão na página: Augusto Cury, Renan Santos, Ronaldo Caiado, Zema e outros. A
correspondência número → nome não foi verificada aqui; o 55 concentrado em GO é compatível
com Caiado.)

O efeito de UF absorve bem o 55, porque ele está concentrado em GO. Mas o 70 e o 14 variam
muito **dentro** de cada UF. Se os eleitores de cada um vão para lados diferentes no 2º turno,
um coeficiente único erra justo onde a mistura é diferente da média, e o erro depende de quais
municípios chegaram primeiro.

Em 2022, a transferência implícita dos eliminados para Lula já variava muito por região: 16%
no Sul, 23% no Centro-Oeste, 25% no Norte, 32% no Sudeste e 41% no Nordeste. A interação
`outros×p1` e os efeitos de região absorvem parte disso, mas só parte.

**Virada sem limites.** A virada é modelada como aditiva na escala da fatia. Pela própria
dedução do modelo, com `o = 7,8%` e `p = 0,49`, a transferência só consegue mover a virada
nacional entre `−o·p ≈ −3,8 p.p.` e `o·(1−p) ≈ +4,0 p.p.`; o comparecimento diferencial
acrescenta pouco (seção 3.7). Mesmo assim, com zero dados, o modelo dá uma faixa de 90% de
**28,5% a 69,2%** (é o `live.json` das 14h30 de hoje). A página não exibe esse número, o que
está certo, mas as primeiras leituras herdam essa prior frouxa (com 2–5% apurado e poucas
cidades grandes, a projeção oscila 1–2 p.p. no teste "grandes primeiro").

**O que fazer.** Fase 3: uma covariável por eliminado relevante (versão barata) e, depois, um
modelo de transferência com taxas entre 0 e 1.

### 3.6 Nenhuma informação externa

O modelo parte do zero: intercepto com prior centrada em 0, ou seja, "os eliminados se dividem
na mesma proporção dos dois no 1º turno". Em 2026, os três maiores eliminados têm correlação
negativa com a força de Lula, e as pesquisas de 2º turno trazem cruzamentos do tipo "voto no
2T por voto no 1T". Também existe o 2º turno de 2022, Lula × Bolsonaro, por município, que
provavelmente prevê muito bem o 2T de 2026 e pode ser cruzado pelo código TSE do município
(o mesmo código de 5 dígitos nos dois arquivos).

O ganho é sobretudo no começo da noite, quando há poucos municípios, a ordem é mais
enviesada e o modelo mais erra. Depois, os dados dominam.

**O que fazer.** Fase 5: prior de transferência vinda de pesquisas e covariáveis históricas,
todas testadas no backtest antes de entrar.

### 3.7 Comparecimento: certo na ideia, pouco relevante

Aqui a crítica é pequena, e é bom dizer isso. Em 2022 o comparecimento variou com o voto (a
razão de válidos 2T/1T foi 1,012 onde Bolsonaro era forte e 0,995 onde Lula era forte), mas,
com a virada perfeitamente conhecida, usar só a razão por UF erraria o resultado em
**+0,04 p.p.**, e uma razão nacional única em +0,09 p.p. Não vale complicar.

Um detalhe barato de corrigir: o choque nacional de comparecimento encolhe com `frac.mean()`
(`modelo.py:169`), a média **não ponderada** da fração apurada por município. No cenário
"pequenos primeiro", com 50% das seções apuradas, esse número é 0,86, quando a fração real
do eleitorado é 0,47. O choque some cedo demais. O efeito na fatia é pequeno.

### 3.8 O cenário do exterior engana mais do que parece

O painel mostra "o resultado final se todo o Brasil tivesse a virada do exterior". Em 2022, a
virada do exterior foi −1,33 p.p. e a do Brasil, −1,69 p.p. O cenário daria **51,52%**
contra 50,90% real: erro de +0,62 p.p. Em 2026, com Lula precisando de +1,0 p.p., esse erro
é 60% da margem que decide a eleição. O exterior é 0,28% dos válidos e tem um perfil
completamente diferente.

**O que fazer.** Mostrar como curiosidade, com o erro histórico ao lado, ou tirar o número
"cenário Brasil".

### 3.9 Comunicação

- A "chance de vitória" aparece com o primeiro município apurado, quando ela depende quase só
  das priors e da extrapolação por porte.
- O bloco "virada sobre o 1º turno" mostra a virada dos municípios já apurados, que no começo
  da noite são os pequenos e os rápidos. É informação útil, mas enviesada, e não diz isso.
- "Intervalo de 90%" promete uma calibração que não foi demonstrada (seção 3.2).

**O que fazer.** Fase 6.

### 3.10 Detalhes operacionais que vazam para a metodologia

- `max_por_ciclo = 1500` prioriza municípios grandes (`poller.py:90-92`). Quando há fila,
  o modelo vê uma amostra "grandes primeiro" diferente da totalização oficial por alguns
  ciclos.
- A mudança é detectada só pelo número de seções totalizadas (`st`). Uma correção de votos
  sem seção nova não é baixada.
- Nada compara a soma dos municípios no estado do poller com o total nacional oficial. Uma
  divergência indicaria dado atrasado ou perdido.

Todos são de baixo risco. O terceiro é o mais útil como alarme.

---

## 4. Tabela de prioridades

| # | Problema | Impacto | Esforço | Fase |
|---|---|---|---|---|
| 3.3 | Backtest com n=1 e ordem irreal | Alto: sem ele, nada é medível | Médio | 1 |
| 3.1 | Linha de base municipal | Alto: viés de ±1,5 p.p. possível, dados de cidades grandes desperdiçados | Alto | 2 |
| 3.2 | Incerteza mal calibrada | Alto: a "chance" é o número mais lido | Médio | 4 |
| 3.4 | σ_mun único e não ponderado | Médio | Baixo | 4 |
| 3.5 | Eliminados num bloco só | Médio, maior em 2026 | Baixo (versão barata) / Alto (transferência) | 3 |
| 3.6 | Sem informação externa | Médio no começo da noite | Médio | 5 |
| 3.8 | Cenário do exterior | Baixo-médio, mas engana | Baixo | 6 |
| 3.9 | Comunicação | Médio | Baixo | 6 |
| 3.7 | Comparecimento e `frac.mean()` | Baixo | Baixo | 4 |
| 3.10 | Operacional | Baixo | Baixo | 6 |

---

## 5. Fase 0: hoje à noite (25/10), sem mexer no código

Mudar modelo em dia de eleição sem backtest é mais arriscado do que qualquer problema acima.
O que dá para fazer é ler o painel sabendo onde ele erra.

1. **Desconfie da chance de vitória antes de ~10% apurado e de algumas capitais grandes com
   dado.** Nessa fase ela depende das priors e da extrapolação por porte.
2. **Se a virada das capitais parcialmente apuradas destoar muito da dos municípios já
   fechados da mesma UF, suspeite de composição** (seção 3.1), não de tendência. O sinal mais
   limpo é a virada dos municípios **100% apurados**.
3. **A faixa de 90% provavelmente está larga demais numa noite normal.** Uma chance de 80% com
   a projeção estável por muito tempo diz mais do que o número sugere. Mas isso vale só se a
   ordem de chegada não for correlacionada com o voto dentro das cidades.
4. **O cenário do exterior errou 0,6 p.p. em 2022.** Não use como prévia.
5. **Se o log do poller disser "N ficam pro próximo ciclo"**, o modelo está alguns ciclos
   atrás da totalização oficial.
6. **Seguro barato para depois:** guarde o estado de cada ciclo. O `historico` só guarda o
   agregado nacional, e a ordem real de chegada por município de hoje é o dado mais valioso
   para o backtest da fase 1. Na cópia principal, o poller (versão não commitada) já grava o
   estado por município no Redis (`estado_poller`) a cada ciclo, mas sobrescreve. Um processo
   à parte que leia essa chave a cada minuto e salve com carimbo de hora resolve, sem tocar no
   poller.

---

## 6. Spec da versão 2

### Fase 1: backtest honesto (pré-requisito)

**Objetivo.** Medir qualquer mudança contra várias eleições, com a ordem de chegada real e
métricas que avaliam a probabilidade, não só a mediana.

**Dados.**
- Eleições de 2º turno com dados por seção nos dois turnos no portal de dados abertos do TSE:
  - presidente 2014, 2018 e 2022 (e 2026 depois de hoje);
  - governador 2014, 2018 e 2022 (dezenas de disputas no total);
  - prefeito 2016, 2020 e 2024 (cerca de 50 cidades por ciclo).
  O método é o mesmo (virada sobre o 1º turno). Mais de 100 disputas permitem calibrar.
- **Ordem real de chegada por seção.** Os arquivos de boletim de urna (`bweb_*`) trazem datas
  por seção. Confirmar no Leiame de cada pacote qual coluna marca o recebimento do boletim
  (não verifiquei o nome exato). Se não houver, usar snapshots arquivados da divulgação ou o
  que for gravado hoje (fase 0, item 6).
- **Aptos por seção** direto do boletim, no lugar do rateio por comparecimento de
  `preparar_2022.py:64`.

**Ordens a testar** (uma biblioteca, todas com a mesma interface):
- real (pelo carimbo de hora);
- as cinco atuais;
- adversas dentro do município (zonas ordenadas pela fatia do 1º turno, nos dois sentidos);
- grandes primeiro;
- uma "adversa calibrada": a correlação entre ordem e voto observada na ordem real, ampliada.

**Métricas**, em cada ponto de 1%, 2%, 5%, 10%, 20%, 30%, 50%, 70% e 90% apurado:
- erro absoluto da mediana nacional e por UF;
- cobertura dos intervalos de 50%, 80% e 90% e histograma PIT;
- CRPS da distribuição projetada;
- Brier e log-loss da P(vitória), calculados **entre disputas**;
- **chamada errada:** algum ponto com P ≥ 95% para quem perdeu. É a métrica de segurança.
- **tempo até a decisão:** primeiro ponto com P ≥ 95% para quem ganhou.

**Separação treino/teste.** Ajustar parâmetros em um grupo de eleições (por exemplo, 2014 e
2018) e reportar o resultado em outro (2022 e governadores/prefeitos de 2022–2024). Registrar
em arquivo qual parâmetro foi ajustado onde.

**Critério de aceite.** Script único que roda todas as disputas × ordens × pontos e gera um
relatório comparando duas versões do modelo. A v1 atual é a primeira linha de comparação.

### Fase 2: linha de base por seção

**Objetivo.** Comparar cada seção apurada com ela mesma no 1º turno e acabar com o viés de
composição.

**Base do 1º turno por seção.** Votação por seção do 1T de 2026 (dados abertos do TSE, ou os
boletins do 1T pelo mesmo caminho de `arquivo-urna` que o `exterior_bu.py` usa), com um
mapeamento seção 1T → seção 2T. Seções criadas ou agregadas entre turnos ficam no modo
municipal.

**Ao vivo.** Generalizar o leitor de boletins do exterior (`exterior_bu.py` + `bu.py`) para
o Brasil, com prioridade por porte. O orçamento de requisições define o desenho:

| Municípios | Eleitorado | Seções |
|---|---|---|
| ≥ 1 mi eleitores (12) | 18% | 82 mil |
| ≥ 300 mil (61) | 33% | 151 mil |
| ≥ 100 mil (221) | 50% | 233 mil |
| Todos | 100% | 499 mil |

Ler tudo custa ~1 milhão de requisições (status + boletim por seção). A 60 req/s, são 4,6 h:
lento demais. Duas opções:
- **Amostra estratificada (recomendado primeiro).** Sortear de antemão ~5% das seções de cada
  município com ≥ 100 mil eleitores (~12 mil seções) e consultar só essas. É o desenho de uma
  contagem paralela por amostragem. Dá uma virada por seção sem viés de composição nas cidades
  que mais pesam, com custo de minutos por ciclo.
- **Leitura completa por prioridade** (capitais primeiro), se o limite de requisições permitir.

Investigar antes se a divulgação publica algum recorte por zona, que seria um meio-termo
barato.

**Mudança no modelo.**
- Onde houver amostra, `y_m` passa a ser a virada **das mesmas seções** e o ruído
  `σ_zona²(1−f)` é trocado pelo erro amostral real da amostra (o RMSE cai de ~2,4 para ~0,3
  p.p. com 20% apurado).
- Onde não houver, mantém-se `y_m` municipal, mas com parte do erro de composição
  **correlacionada entre municípios** (um termo comum a todas as cidades parciais).

**Critério de aceite.** Na biblioteca de ordens, o viés do cenário "zonas anti-Lula primeiro
dentro da cidade" cai de −1,6 p.p. para menos de 0,3 p.p. com 5% apurado, sem piorar o
cenário aleatório.

### Fase 3: o modelo da virada

**3a. Versão barata (primeiro).** Trocar `outros` por uma fatia por eliminado relevante (≥ 1%
dos válidos) mais um "resto", cada uma com interação com `p1`. Continua linear e continua
resolvida pelas mesmas equações de modelo misto. Testar no backtest com 2018 (vários
eliminados grandes) e 2022 (eliminados de perfis diferentes).

**3b. Modelo de transferência (depois, se a 3a mostrar ganho).** Para cada município:

```
A2_m = Σ_j r_jA · N_jm        B2_m = Σ_j r_jB · N_jm
j ∈ {A1, B1, cada eliminado k, brancos e nulos, abstenção}
r_jA + r_jB + r_j,branco/nulo + r_j,abstenção = 1,   0 ≤ r ≤ 1
```

com as taxas `r` variando por UF em torno de uma média nacional (efeito aleatório em escala
logit). Vantagens: limites físicos automáticos, comparecimento explicado no mesmo modelo,
resultado interpretável ("62% dos eleitores do 55 foram para Flávio") e um lugar natural para
a prior de pesquisa. Riscos: inferência ecológica (taxas que variam dentro do município),
custo computacional (pode exigir um amostrador) e mais coisa para dar errado ao vivo. Só
entra se ganhar da 3a no backtest.

### Fase 4: incerteza

- **σ_mun heterocedástico:** em 2022 o resíduo cai com o porte até ~50 mil eleitores e fica
  plano acima disso, o que a forma `σ_mun² = s∞² + c / eleitores_m` reproduz razoavelmente
  (s∞ ≈ 0,55 p.p., c ≈ 0,6). Estimar `s∞` e `c` por REML. Remover o piso de 0,5 p.p. e o
  `weights=w*0+1`.
- **Componentes de variância estimados** (σ_UF, σ_região) por REML a cada leitura, com prior
  vinda do histórico (2022: ~0,45 p.p.), em vez de 1,5 p.p. fixos.
- **Erro "tardio" que cresce com a diferença entre apurado e restante.** Por exemplo,
  proporcional à distância (Mahalanobis) entre a distribuição das covariáveis no que já chegou
  e no que falta, ponderada por votos. Quando o apurado é parecido com o resto, a faixa
  estreita; quando chegaram só cidades pequenas do Sul, ela alarga.
- **Resíduos t de Student** no lugar dos gaussianos, para não deixar um município estranho
  mover tudo no começo da noite.
- **`frac` ponderado pelo eleitorado** no choque nacional de comparecimento.
- **Calibração final:** um fator de escala global da incerteza, ajustado no conjunto de
  treino para que o intervalo de 90% cubra ~90%, e conferido no de teste.

**Critério de aceite.** No conjunto de teste: cobertura do intervalo de 90% entre 85% e 95%,
**zero chamadas erradas a 95%**, e Brier da P(vitória) melhor que o da v1.

### Fase 5: informação externa

- **Prior de pesquisa** sobre a virada nacional (v1/3a) ou sobre as taxas de transferência
  (3b), com variância igual ao erro histórico das pesquisas de 2º turno, para que caia rápido.
- **2º turno de 2022 por município** como covariável (`p2_2022 − p1_2026`, por exemplo),
  cruzado pelo código TSE.
- **Covariáveis do Censo 2022** (renda, urbanização, religião), pelo código IBGE que já está
  na base.

Cada uma entra só se melhorar o backtest. A de 2022 só pode ser testada direito em 2026 (ou,
como análogo, usando 2018 → 2022).

### Fase 6: comunicação e operação

- Mostrar a chance de vitória só depois de um limiar mínimo (por exemplo ≥ 5% do eleitorado
  e dado em ≥ 15 UFs) ou exibi-la como faixa ("entre 60% e 80%") nas primeiras leituras.
- Indicador simples de "quão parecido o apurado está com o que falta", com a mesma métrica da
  fase 4.
- Rótulo no bloco de virada: "nos municípios já apurados, que no começo da noite são os
  menores".
- Cenário do exterior com o erro de 2022 ao lado, ou sem o número "cenário Brasil".
- Alarme quando a soma dos municípios no poller divergir do total oficial em mais de 0,5% dos
  válidos.
- Um histórico de mudanças da metodologia na própria página.
- **Modo sombra:** rodar a v2 em paralelo à v1 numa eleição real (os 2º turnos municipais de
  2028, por exemplo) antes de trocar.

---

## 7. O que não fazer

- **Não mexer no modelo hoje.**
- **Não ajustar parâmetros olhando para o conjunto de teste.** É o jeito mais rápido de ter
  um backtest bonito e um modelo ruim.
- **Não estreitar a faixa só porque ela cobriu 45 de 45 em 2022.** Primeiro o backtest com
  várias eleições, depois a calibração.
- **Não pular para o modelo de transferência completo** antes de ver o ganho da versão barata.
- **Não tratar os testes extremos da seção 3.2 como prováveis.** Eles mostram um modo de
  falha, não uma previsão.

---

## Apêndice: como os números foram obtidos

- Nada no repositório foi alterado. Os scripts importam `modelo.py` e `backtest_2022.py` sem
  mudanças e leem `data/secoes_2022.npz` e `data/backtest_2022.json` da cópia principal.
- Scripts em
  `/private/tmp/claude-501/-Users-lucca-repos-acompanhe-as-eleicoes--claude-worktrees-election-forecast-methodology-review-f1a04d/ba0865b5-5b39-4f9a-ba98-00ac97d1243a/scratchpad/analise/`
  (pasta temporária; peça para trazer para o repo se quiser guardar):
  - `a1_empirico_2022.py`: viradas por UF e região, transferência implícita, resíduo por
    porte, comparecimento por força do Lula, exterior × Brasil;
  - `a2_ordem.py`: parcial ingênuo da "ordem real" do backtest e variações;
  - `a3_estresse.py`: viés de composição (cidades com 4+ zonas) e as ordens extremas;
  - `a4_estresse2.py`: composição dentro da cidade isolada, e "grandes primeiro";
  - `a5_comparec.py`: peso do comparecimento dentro da UF e `frac.mean()`;
  - `a6_calibracao.py`: 12 ordens sorteadas por cenário, erro real × incerteza declarada.
- Os testes de modelo usam 2.000–3.000 simulações (o painel usa 10 mil). A diferença afeta
  só a terceira casa das probabilidades.
- O λ da seção 3.1 é calculado pela fórmula de `modelo.py:151` com σ_mun = 0,5 p.p. (o valor
  que os testes mostraram na maioria das leituras) e σ_zona = 2 p.p. A coluna "com base por
  seção" usa σ_mun ≈ 0,55 p.p. (cidades grandes, seção 3.4) e o erro de 0,3 p.p. medido na
  seção 3.1; é uma estimativa.
- Base de 2026: `data/base_1t_2026.json` (5.757 municípios e cidades do exterior, Lula 45,16%,
  Flávio 47,03%, 48,99% entre os dois).
