"""Orquestrador: passa cada vídeo/corte pelos agentes, guardando o progresso no banco.

Cada etapa é retomável: se o processo cair no meio, a próxima rodada continua de onde parou.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agentes import baixador, cacador, curador, editor, publicador, revisor, transcritor
from .config import Config
from .db import Banco
from .youtube import servico

log = logging.getLogger(__name__)


def _corte_dict(linha) -> dict:
    d = dict(linha)
    d["hashtags"] = json.loads(d["hashtags"] or "[]")
    return d


def etapa_cacar(cfg: Config, banco: Banco, yt) -> None:
    pendentes = len(banco.videos("encontrado")) + len(banco.videos("baixado")) + len(banco.videos("transcrito"))
    if pendentes >= cfg.cacador.videos_por_rodada:
        log.info("caçador: %d vídeos ainda na fila, pulando busca", pendentes)
        return
    cacador.rodar(cfg, banco, yt)


def etapa_preparar(cfg: Config, banco: Banco) -> None:
    pasta = cfg.pasta_dados / "originais"
    for v in banco.videos("encontrado"):
        try:
            arquivo = baixador.baixar(v["id"], pasta)
            banco.atualizar_video(v["id"], status="baixado", arquivo=str(arquivo))
        except Exception as e:  # noqa: BLE001 — um vídeo com problema não pode parar a fila
            log.exception("falha ao baixar %s", v["id"])
            banco.atualizar_video(v["id"], status="erro", erro=f"download: {e}")

    for v in banco.videos("baixado"):
        try:
            t = transcritor.transcrever(Path(v["arquivo"]), cfg.transcricao)
            banco.atualizar_video(v["id"], status="transcrito", transcricao=str(t))
        except Exception as e:  # noqa: BLE001
            log.exception("falha ao transcrever %s", v["id"])
            banco.atualizar_video(v["id"], status="erro", erro=f"transcrição: {e}")

    for v in banco.videos("transcrito"):
        try:
            cortes = curador.curar(Path(v["transcricao"]), v["titulo"], v["duracao_seg"], cfg.curador)
            for c in cortes:
                banco.inserir_corte(
                    v["id"], inicio=c["inicio"], fim=c["fim"], titulo=c["titulo"], descricao=c["descricao"],
                    hashtags=c["hashtags"], gancho=c["gancho"], nota=c["nota"],
                )
            banco.atualizar_video(v["id"], status="curado" if cortes else "descartado")
        except Exception as e:  # noqa: BLE001
            log.exception("falha na curadoria de %s", v["id"])
            banco.atualizar_video(v["id"], status="erro", erro=f"curadoria: {e}")


def etapa_editar(cfg: Config, banco: Banco) -> None:
    por_video = defaultdict(list)
    for c in banco.cortes("curado"):
        por_video[c["video_id"]].append(_corte_dict(c))

    for video_id, cortes in por_video.items():
        v = banco.video(video_id)
        transcricao = Path(v["transcricao"])
        for c in cortes:
            try:
                r = revisor.revisar(transcricao, c)
                if not r["aprovado"]:
                    banco.atualizar_corte(c["id"], status="reprovado", erro="; ".join(r["problemas"]))
                    continue
                c.update(titulo=r["titulo"], descricao=r["descricao"], hashtags=r["hashtags"])
                saida = cfg.pasta_dados / "shorts" / f"{video_id}_{c['id']}.mp4"
                editor.editar(Path(v["arquivo"]), transcricao, c, saida, cfg.editor)
                banco.atualizar_corte(
                    c["id"], status="editado", arquivo=str(saida), titulo=c["titulo"], descricao=c["descricao"],
                    hashtags=json.dumps(c["hashtags"], ensure_ascii=False),
                )
            except Exception as e:  # noqa: BLE001
                log.exception("falha ao editar corte %s", c["id"])
                banco.atualizar_corte(c["id"], status="erro", erro=f"edição: {e}")
        # todos os cortes desse vídeo foram editados: o original não é mais necessário
        Path(v["arquivo"]).unlink(missing_ok=True)


def etapa_publicar(cfg: Config, banco: Banco, yt) -> None:
    p = cfg.publicador
    agora = datetime.now(timezone.utc)
    feitos = banco.publicados_desde((agora - timedelta(hours=24)).isoformat())
    vagas = max(p.max_postagens_por_dia - feitos, 0)
    if not vagas:
        log.info("publicador: limite diário atingido (%d)", p.max_postagens_por_dia)
        return

    for c in banco.cortes("editado")[:vagas]:
        c = _corte_dict(c)
        v = dict(banco.video(c["video_id"]))
        try:
            meta = publicador.montar_metadados(c, v, p)
            # só agenda quando o destino é público; em modo de teste (private/unlisted) sobe direto
            quando = publicador.proximo_horario(banco.ultimo_agendamento(), p) if p.privacidade == "public" else None
            yt_id = publicador.publicar(yt, Path(c["arquivo"]), meta, quando)
            banco.atualizar_corte(
                c["id"], status="publicado", youtube_id=yt_id,
                publicado_em=datetime.now(timezone.utc).isoformat(),
                publicar_em=quando.isoformat() if quando else None,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("falha ao publicar corte %s", c["id"])
            banco.atualizar_corte(c["id"], status="erro", erro=f"publicação: {e}")
            if "quotaExceeded" in str(e):
                break


def rodar(cfg: Config, publicar: bool = True) -> None:
    banco = Banco(cfg.pasta_dados / "cortes.db")
    yt = servico()
    etapa_cacar(cfg, banco, yt)
    etapa_preparar(cfg, banco)
    etapa_editar(cfg, banco)
    if publicar:
        etapa_publicar(cfg, banco, yt)
