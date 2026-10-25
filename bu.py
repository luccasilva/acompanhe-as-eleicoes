"""Leitor mínimo do boletim de urna (arquivo .bu.dat) publicado pelo TSE.

O arquivo é um envelope ASN.1 (BER) cujo conteúdo é outro BER, a EntidadeBoletimUrna.
Não precisamos do esquema inteiro: basta percorrer a árvore e reconhecer

- o resultado por eleição: SEQUENCE que começa pelo id da eleição, seguido dos eleitores aptos;
- cada TotalVotosVotavel: SEQUENCE com [1] tipo do voto, [2] quantidade e, para votos
  nominais, [3] a identificação do votável (partido, número).
"""

CONTEXTO, UNIVERSAL = 2, 0
INTEIRO, OCTETOS, SEQUENCIA = 2, 4, 16


def _tlv(buf, i):
    t = buf[i]
    i += 1
    classe, construido, num = t >> 6, (t >> 5) & 1, t & 0x1F
    if num == 0x1F:
        num = 0
        while True:
            b = buf[i]
            i += 1
            num = (num << 7) | (b & 0x7F)
            if not b & 0x80:
                break
    n = buf[i]
    i += 1
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return classe, construido, num, i, i + n


def _arvore(buf, ini=0, fim=None):
    """Lista de nós (classe, construido, num, valor_ou_filhos)."""
    fim = len(buf) if fim is None else fim
    nos = []
    i = ini
    while i < fim:
        classe, construido, num, a, b = _tlv(buf, i)
        nos.append((classe, construido, num, _arvore(buf, a, b) if construido else buf[a:b]))
        i = b
    return nos


def _int(v):
    return int.from_bytes(v, "big", signed=True)


def _percorrer(nos):
    for no in nos:
        yield no
        if no[1]:
            yield from _percorrer(no[3])


def ler_bu(dados, id_eleicao):
    """Devolve {'aptos', 'comparec', 'votos': {numero: qtd}, 'outros'} da eleição pedida."""
    envelope = _arvore(dados)
    conteudo = next(v for c, k, n, v in _percorrer(envelope)
                    if c == UNIVERSAL and n == OCTETOS and not k and len(v) > 100)
    nos = _arvore(conteudo)

    aptos = None
    for classe, construido, num, filhos in _percorrer(nos):
        if (classe, num) == (UNIVERSAL, SEQUENCIA) and construido and len(filhos) >= 2:
            f0, f1 = filhos[0], filhos[1]
            if (f0[0], f0[2], f1[0], f1[2]) == (UNIVERSAL, INTEIRO, UNIVERSAL, INTEIRO) \
                    and not f0[1] and _int(f0[3]) == int(id_eleicao):
                aptos = _int(f1[3])
                break
    if aptos is None:
        raise ValueError(f"eleição {id_eleicao} não encontrada no boletim")

    votos, outros = {}, 0
    for classe, construido, num, filhos in _percorrer(nos):
        if not (construido and (classe, num) == (UNIVERSAL, SEQUENCIA) and len(filhos) >= 2):
            continue
        tipo, qtd = filhos[0], filhos[1]
        if (tipo[0], tipo[2], qtd[0], qtd[2]) != (CONTEXTO, 1, CONTEXTO, 2) or tipo[1] or qtd[1]:
            continue
        ident = next((f for f in filhos[2:] if f[0] == CONTEXTO and f[2] == 3 and f[1]), None)
        if ident:
            numero = str(_int(ident[3][-1][3]))
            votos[numero] = votos.get(numero, 0) + _int(qtd[3])
        else:
            outros += _int(qtd[3])  # brancos, nulos
    return {"aptos": aptos, "votos": votos, "outros": outros, "comparec": sum(votos.values()) + outros}
