"""Agente Baixador: baixa o vídeo escolhido (até 1080p, mp4)."""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import Baixador

log = logging.getLogger(__name__)


def baixar(video_id: str, pasta: Path, cfg: Baixador | None = None) -> Path:
    import yt_dlp

    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{video_id}.mp4"
    if destino.exists():
        return destino
    opcoes = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(pasta / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
    }
    if cfg and cfg.cookies_navegador:
        opcoes["cookiesfrombrowser"] = (cfg.cookies_navegador,)
    if cfg and cfg.cookies_arquivo:
        opcoes["cookiefile"] = cfg.cookies_arquivo
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    if not destino.exists():
        raise RuntimeError(f"download de {video_id} não gerou {destino}")
    log.info("baixador: %s salvo em %s", video_id, destino)
    return destino
