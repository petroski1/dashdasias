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
- começa e termina em limites de frase (use os tempos da transcrição).

Os títulos devem ser fiéis ao que é dito no trecho (nada de clickbait enganoso), no idioma pedido,
em tom de rede social. Hashtags sem o símbolo #, de 3 a 5, relevantes ao tema."""


def _formatar_transcricao(segmentos: list[dict]) -> str:
    return "\n".join(f"[{s['i']:.1f}–{s['f']:.1f}] {s['texto']}" for s in segmentos)


def ajustar_cortes(propostas: list[dict], palavras: list[dict], duracao_video: float, cfg: Curador) -> list[dict]:
    """Encaixa início/fim nos limites das palavras, aplica limites de duração, nota e remove sobreposições."""
    aceitos: list[dict] = []
    for p in sorted(propostas, key=lambda x: x["nota"], reverse=True):
        if p["nota"] < cfg.nota_minima:
            continue
        dentro = [w for w in palavras if w["i"] >= p["inicio"] - 0.4 and w["f"] <= p["fim"] + 0.4]
        if not dentro:
            continue
        inicio = max(dentro[0]["i"] - 0.15, 0.0)
        fim = min(dentro[-1]["f"] + 0.25, duracao_video)
        # se passou do limite, corta na última palavra que ainda cabe
        while fim - inicio > cfg.duracao_max_seg and len(dentro) > 1:
            dentro.pop()
            fim = dentro[-1]["f"] + 0.25
        if not (cfg.duracao_min_seg <= fim - inicio <= cfg.duracao_max_seg):
            continue
        if any(inicio < a["fim"] and fim > a["inicio"] for a in aceitos):
            continue
        aceitos.append({**p, "inicio": round(inicio, 2), "fim": round(fim, 2)})
        if len(aceitos) >= cfg.cortes_por_video:
            break
    return sorted(aceitos, key=lambda x: x["inicio"])


def curar(transcricao: Path, titulo_video: str, duracao_video: float, cfg: Curador) -> list[dict]:
    dados = json.loads(transcricao.read_text(encoding="utf-8"))
    pedido = (
        f"Vídeo: {titulo_video}\nIdioma dos títulos: {cfg.idioma}\n"
        f"Proponha {cfg.cortes_por_video * 2} cortes de {cfg.duracao_min_seg:.0f} a {cfg.duracao_max_seg:.0f} segundos, "
        f"sem sobreposição. Seja exigente com as notas.\n\n"
        f"<transcricao>\n{_formatar_transcricao(dados['segmentos'])}\n</transcricao>"
    )
    resp = llm.perguntar_json(SISTEMA, pedido, ESQUEMA, esforco="high")
    cortes = ajustar_cortes(resp["cortes"], dados["palavras"], duracao_video, cfg)
    log.info("curador: %d propostas, %d aprovadas (nota mínima %s)", len(resp["cortes"]), len(cortes), cfg.nota_minima)
    for p in sorted(resp["cortes"], key=lambda x: x["nota"], reverse=True):
        log.info("curador:   nota %s — %s (%.0fs) — %s", p["nota"], p["titulo"], p["fim"] - p["inicio"], p["motivo"][:160])
    return cortes
