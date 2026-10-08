"""Agente Revisor: confere se título/descrição batem com o que é dito no corte antes de publicar."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import llm

log = logging.getLogger(__name__)

ESQUEMA = {
    "type": "object",
    "properties": {
        "aprovado": {"type": "boolean"},
        "titulo": {"type": "string", "description": "título final, até 90 caracteres"},
        "descricao": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "problemas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["aprovado", "titulo", "descricao", "hashtags", "problemas"],
    "additionalProperties": False,
}

SISTEMA = """Você é o Agente Revisor de uma operação de cortes para YouTube Shorts. Antes de publicar, você confere:
1. O corte faz sentido sozinho (começo e fim completos)?
2. O título é fiel ao que é dito (sem prometer algo que o trecho não entrega)?
3. Há algo que viole as políticas do YouTube (discurso de ódio, desinformação médica, conteúdo sexual,
   violência explícita, dados pessoais de terceiros)?
Reprove (aprovado=false) se 1 ou 3 falharem. Se só o título/descrição precisarem de ajuste, corrija e aprove.
Mantenha o idioma original. Hashtags sem o símbolo #."""


def revisar(transcricao: Path, corte: dict) -> dict:
    palavras = json.loads(transcricao.read_text(encoding="utf-8"))["palavras"]
    fala = " ".join(w["p"] for w in palavras if corte["inicio"] - 0.05 <= w["i"] and w["f"] <= corte["fim"] + 0.05)
    pedido = (
        f"<fala_do_corte>\n{fala}\n</fala_do_corte>\n\n"
        f"Título proposto: {corte['titulo']}\nDescrição proposta: {corte['descricao']}\n"
        f"Hashtags propostas: {', '.join(corte['hashtags'])}"
    )
    resp = llm.perguntar_json(SISTEMA, pedido, ESQUEMA, esforco="low")
    if not resp["aprovado"]:
        log.info("revisor: reprovado — %s", "; ".join(resp["problemas"]))
    return resp
