"""Agente Curador: lê a transcrição e escolhe os melhores trechos para virar Shorts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import llm
from ..config import Curador

log = logging.getLogger(__name__)

ESQUEMA = {
    "type": "object",
    "properties": {
        "cortes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "number", "description": "segundos no vídeo original"},
                    "fim": {"type": "number", "description": "segundos no vídeo original"},
                    "gancho": {"type": "string", "description": "frase curta (até 6 palavras) exibida nos 3 primeiros segundos"},
                    "titulo": {"type": "string", "description": "até 90 caracteres, sem #Shorts"},
                    "descricao": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "nota": {"type": "number", "description": "0 a 10, potencial de viralizar"},
                    "motivo": {"type": "string"},
                },
                "required": ["inicio", "fim", "gancho", "titulo", "descricao", "hashtags", "nota", "motivo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cortes"],
    "additionalProperties": False,
}

SISTEMA = """Você é o Agente Curador de uma operação de cortes para YouTube Shorts.
Você recebe a transcrição com tempos de um vídeo longo e escolhe os trechos com maior chance de viralizar.

Um bom corte:
- começa forte: a primeira frase já prende (pergunta, afirmação polêmica, número, começo de história);
- faz sentido sozinho, sem contexto do resto do vídeo;
- termina numa conclusão, punchline ou frase de impacto — nunca no meio de uma ideia;
- começa no início de uma frase e termina no ponto final de uma frase (use os tempos da transcrição;
  linhas marcadas com ‖ vêm depois de uma pausa na fala, bons pontos de corte);
  um trecho que precisaria passar do tempo máximo para fechar a ideia não serve — escolha outro.

Os títulos devem ser fiéis ao que é dito no trecho (nada de clickbait enganoso), no idioma pedido,
em tom de rede social. Hashtags sem o símbolo #, de 3 a 5, relevantes ao tema."""


def _formatar_transcricao(segmentos: list[dict]) -> str:
    """Uma linha por segmento; '‖' marca uma pausa longa antes do segmento (bom lugar para começar ou terminar)."""
    linhas = []
    for k, s in enumerate(segmentos):
        pausa = k > 0 and s["i"] - segmentos[k - 1]["f"] >= PAUSA_FIM_DE_FRASE
        linhas.append(f"{'‖ ' if pausa else ''}[{s['i']:.1f}–{s['f']:.1f}] {s['texto']}")
    return "\n".join(linhas)


FINAIS = (".", "?", "!", "…")
PAUSA_FIM_DE_FRASE = 0.5  # segundos de silêncio que contam como fim de frase


def _termina_frase(palavra: str) -> bool:
    return palavra.rstrip("\"'”»)]").endswith(FINAIS)


def fins_de_frase(palavras: list[dict]) -> list[bool]:
    """Marca as palavras que fecham uma frase: pontuação final ou pausa na fala logo depois.
    Se a transcrição não tiver nem uma coisa nem outra (raro), qualquer palavra serve de limite."""
    fins = [
        _termina_frase(w["p"]) or k + 1 == len(palavras) or palavras[k + 1]["i"] - w["f"] >= PAUSA_FIM_DE_FRASE
        for k, w in enumerate(palavras)
    ]
    if sum(fins) < len(palavras) / 80:
        return [True] * len(palavras)
    return fins


def _limites(palavras: list[dict], p: dict, duracao_video: float, cfg: Curador, fins: list[bool]) -> tuple[float, float] | None:
    """Encaixa o corte em começo e fim de frase. Devolve (inicio, fim) ou None se não houver encaixe bom."""
    idx = [k for k, w in enumerate(palavras) if w["i"] >= p["inicio"] - 0.4 and w["f"] <= p["fim"] + 0.4]
    if not idx:
        return None
    a, b = idx[0], idx[-1]
    termina = fins.__getitem__
    comeca = lambda k: k == 0 or termina(k - 1)  # noqa: E731

    # início: recua até o começo da frase (no máximo 4s); se não der, avança para a próxima frase
    k = a
    while not comeca(k) and palavras[a]["i"] - palavras[k - 1]["i"] <= 4:
        k -= 1
    if not comeca(k):
        k = a
        while k < b and not comeca(k):
            k += 1
        if not comeca(k):
            return None
    a = k

    def cabe(j: int) -> bool:
        return palavras[j]["f"] - palavras[a]["i"] + 0.4 <= cfg.duracao_max_seg

    # fim: estende até o ponto final (no máximo 4s, sem passar do limite); se não der, recua
    j = b
    while not termina(j) and j + 1 < len(palavras) and palavras[j + 1]["f"] - palavras[b]["f"] <= 4 and cabe(j + 1):
        j += 1
    if not (termina(j) and cabe(j)):
        j = b
        while j > a and not (termina(j) and cabe(j)):
            j -= 1
        if not (termina(j) and cabe(j)):
            return None
    return max(palavras[a]["i"] - 0.15, 0.0), min(palavras[j]["f"] + 0.25, duracao_video)


def ajustar_cortes(propostas: list[dict], palavras: list[dict], duracao_video: float, cfg: Curador) -> list[dict]:
    """Encaixa cada corte em frases completas, aplica limites de duração e nota e remove sobreposições."""
    fins = fins_de_frase(palavras)
    aceitos: list[dict] = []
    for p in sorted(propostas, key=lambda x: x["nota"], reverse=True):
        if p["nota"] < cfg.nota_minima:
            continue
        limites = _limites(palavras, p, duracao_video, cfg, fins)
        if not limites:
            continue
        inicio, fim = limites
        if not (cfg.duracao_min_seg <= fim - inicio <= cfg.duracao_max_seg):
            continue
        if any(inicio < a["fim"] and fim > a["inicio"] for a in aceitos):
            continue
        aceitos.append({**p, "inicio": round(inicio, 2), "fim": round(fim, 2)})
        if len(aceitos) >= cfg.cortes_por_video:
            break
    return sorted(aceitos, key=lambda x: x["inicio"])


def curar(transcricao: Path, titulo_video: str, duracao_video: float, cfg: Curador, feedback: str = "") -> list[dict]:
    dados = json.loads(transcricao.read_text(encoding="utf-8"))
    pedido = (
        f"Vídeo: {titulo_video}\nIdioma dos títulos: {cfg.idioma}\n"
        f"Proponha {cfg.cortes_por_video * 2} cortes de {cfg.duracao_min_seg:.0f} a {cfg.duracao_max_seg:.0f} segundos, "
        f"sem sobreposição. Seja exigente com as notas.\n"
    )
    if feedback:
        pedido += (
            "\nVocê já curou este vídeo e o Revisor reprovou os cortes. Não repita os mesmos erros "
            f"(se precisar, escolha outros trechos):\n<reprovacoes>\n{feedback}\n</reprovacoes>\n"
        )
    pedido += f"\n<transcricao>\n{_formatar_transcricao(dados['segmentos'])}\n</transcricao>"
    resp = llm.perguntar_json(SISTEMA, pedido, ESQUEMA, esforco="high")
    cortes = ajustar_cortes(resp["cortes"], dados["palavras"], duracao_video, cfg)
    log.info("curador: %d propostas, %d aprovadas (nota mínima %s)", len(resp["cortes"]), len(cortes), cfg.nota_minima)
    for p in sorted(resp["cortes"], key=lambda x: x["nota"], reverse=True):
        log.info("curador:   nota %s — %s (%.0fs) — %s", p["nota"], p["titulo"], p["fim"] - p["inicio"], p["motivo"][:160])
    return cortes
