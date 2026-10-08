"""Chamada ao Claude com resposta em JSON validado."""
from __future__ import annotations

import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

MODELO = os.environ.get("CORTES_MODELO", "claude-opus-5-5")

_cliente: anthropic.Anthropic | None = None


def cliente() -> anthropic.Anthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic()
    return _cliente


class RespostaRecusada(RuntimeError):
    pass


def perguntar_json(sistema: str, usuario: str, esquema: dict, esforco: str = "medium") -> dict:
    """Envia o pedido e devolve o JSON da resposta, já no formato do esquema."""
    with cliente().beta.messages.stream(
        model=MODELO,
        max_tokens=64000,
        system=sistema,
        messages=[{"role": "user", "content": usuario}],
        output_config={"effort": esforco, "format": {"type": "json_schema", "schema": esquema}},
        # Se um classificador de segurança recusar o pedido, a API tenta de novo em outro modelo.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        resposta = stream.get_final_message()

    if resposta.stop_reason == "refusal":
        raise RespostaRecusada(getattr(resposta.stop_details, "explanation", None) or "pedido recusado")
    if resposta.stop_reason == "max_tokens":
        raise RuntimeError("resposta cortada por max_tokens")

    texto = next(b.text for b in resposta.content if b.type == "text")
    log.debug("uso: %s", resposta.usage)
    return json.loads(texto)
