"""Agente Planejador: olha o estado de cada vídeo e corte e cria as tarefas que faltam.

É feito em código, sem IA, de propósito: decidir "o vídeo X foi baixado, então falta transcrever"
é uma regra fixa, e precisa ser rápido, de graça e nunca esquecer nada nem criar tarefa repetida.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..trabalhos import Contexto

log = logging.getLogger(__name__)

# status do vídeo -> próxima tarefa
PROXIMA_DO_VIDEO = {"encontrado": "baixar", "baixado": "transcrever", "transcrito": "curar"}


def planejar(ctx: Contexto) -> list[tuple[str, str]]:
    cfg, banco = ctx.cfg, ctx.banco
    criadas: list[tuple[str, str]] = []

    def criar(tipo: str, alvo: str = "") -> None:
        if banco.criar_tarefa(tipo, alvo) is not None:
            criadas.append((tipo, alvo))

    # 1. buscar vídeos novos, se a fila estiver curta e a última busca não for recente
    fila = sum(len(banco.videos(s)) for s in PROXIMA_DO_VIDEO)
    ultima = banco.ultima_execucao("cacar")
    recente = ultima and datetime.fromisoformat(ultima) > datetime.now(timezone.utc) - timedelta(
        hours=cfg.gerencia.intervalo_caca_horas
    )
    if fila < cfg.cacador.videos_por_rodada and not recente:
        criar("cacar")

    # 2. levar cada vídeo para a próxima etapa
    for status, tipo in PROXIMA_DO_VIDEO.items():
        for v in banco.videos(status):
            criar(tipo, v["id"])

    # 3. editar os cortes aprovados pelo curador
    for c in banco.cortes("curado"):
        criar("editar", str(c["id"]))

    # 4. publicar, respeitando pausa e limite diário
    if ctx.publicar and banco.ajuste("publicacao_pausada") != "1":
        agora = datetime.now(timezone.utc)
        feitos = banco.publicados_desde((agora - timedelta(hours=24)).isoformat())
        vagas = cfg.publicador.max_postagens_por_dia - feitos - banco.tarefas_abertas("publicar")
        for c in banco.cortes("editado")[: max(vagas, 0)]:
            criar("publicar", str(c["id"]))

    if criadas:
        log.info("planejador: %d tarefa(s) nova(s): %s", len(criadas), ", ".join(f"{t}:{a}" if a else t for t, a in criadas))
    return criadas
