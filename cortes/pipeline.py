"""Ponto de entrada de uma rodada.

Com o Gerente ligado, o Claude comanda a rodada (veja agentes/gerente.py). Sem ele, roda o ciclo fixo:
Planejador cria as tarefas e o Executor executa, encadeando as etapas.
"""
from __future__ import annotations

import logging

from .agentes import executor, gerente, planejador
from .config import Config
from .db import Banco
from .trabalhos import Contexto

log = logging.getLogger(__name__)


def contexto(cfg: Config, publicar: bool = True) -> Contexto:
    return Contexto(cfg=cfg, banco=Banco(cfg.pasta_dados / "cortes.db"), publicar=publicar)


def rodar(cfg: Config, publicar: bool = True, usar_gerente: bool | None = None) -> str:
    ctx = contexto(cfg, publicar)
    if cfg.gerencia.usar_gerente if usar_gerente is None else usar_gerente:
        return gerente.rodar(ctx)
    planejador.planejar(ctx)
    return executor.executar(ctx).texto()
