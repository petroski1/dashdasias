"""Agente Publicador: envia o Short para o YouTube, com agendamento e limite diário de cota."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Publicador

log = logging.getLogger(__name__)


def montar_metadados(corte: dict, video_origem: dict, cfg: Publicador) -> dict:
    hashtags = [h.lstrip("#").replace(" ", "") for h in corte["hashtags"] if h.strip("# ")]
    titulo = corte["titulo"].strip()
    if "#shorts" not in titulo.lower() and len(titulo) <= 100 - len(" #Shorts"):
        titulo += " #Shorts"
    descricao = corte["descricao"].strip()
    if cfg.creditar_fonte:
        descricao += (
            f"\n\nFonte: \"{video_origem['titulo']}\" — {video_origem['canal']}\n"
            f"https://www.youtube.com/watch?v={video_origem['id']}"
        )
    descricao += "\n\n" + " ".join(f"#{h}" for h in hashtags + ["Shorts"])
    return {
        "snippet": {
            "title": titulo[:100],
            "description": descricao[:5000],
            "tags": hashtags[:15],
            "categoryId": cfg.categoria_id,
        },
        "status": {"privacyStatus": cfg.privacidade, "selfDeclaredMadeForKids": False},
    }


def proximo_horario(ultimo_iso: str | None, cfg: Publicador, agora: datetime | None = None) -> datetime | None:
    """Horário da próxima publicação agendada, ou None para publicar imediatamente."""
    if cfg.intervalo_horas <= 0:
        return None
    agora = agora or datetime.now(timezone.utc)
    base = agora + timedelta(minutes=15)
    if ultimo_iso:
        ultimo = datetime.fromisoformat(ultimo_iso) + timedelta(hours=cfg.intervalo_horas)
        base = max(base, ultimo)
    return base.replace(microsecond=0)


def publicar(yt, arquivo: Path, metadados: dict, publicar_em: datetime | None) -> str:
    from googleapiclient.http import MediaFileUpload

    corpo = json.loads(json.dumps(metadados))
    if publicar_em is not None:
        # o YouTube só agenda vídeos privados; na hora marcada ele fica público
        corpo["status"]["privacyStatus"] = "private"
        corpo["status"]["publishAt"] = publicar_em.isoformat().replace("+00:00", "Z")

    midia = MediaFileUpload(str(arquivo), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    pedido = yt.videos().insert(part="snippet,status", body=corpo, media_body=midia)
    resposta = None
    while resposta is None:
        _, resposta = pedido.next_chunk()
    log.info("publicador: %s -> https://youtube.com/shorts/%s", arquivo.name, resposta["id"])
    return resposta["id"]


def enviar_capa(yt, video_id: str, capa: Path) -> bool:
    """Define a thumbnail. Exige canal verificado por telefone; se falhar, o vídeo continua no ar."""
    from googleapiclient.http import MediaFileUpload

    try:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(capa), mimetype="image/jpeg")).execute()
        log.info("publicador: capa enviada para %s", video_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("publicador: não foi possível enviar a capa de %s: %s", video_id, e)
        return False
