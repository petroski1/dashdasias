"""Agente Transcritor: transcreve o vídeo com tempo de cada palavra (faster-whisper)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Transcricao

log = logging.getLogger(__name__)

_modelo = None


def _carregar_modelo(cfg: Transcricao):
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel

        tipo = "float16" if cfg.dispositivo == "cuda" else "int8"
        _modelo = WhisperModel(cfg.modelo_whisper, device=cfg.dispositivo, compute_type=tipo)
    return _modelo


def transcrever(video: Path, cfg: Transcricao) -> Path:
    """Gera <video>.json com {"segmentos": [{i, f, texto}], "palavras": [{i, f, p}]}."""
    destino = video.with_suffix(".json")
    if destino.exists():
        return destino
    modelo = _carregar_modelo(cfg)
    segmentos, info = modelo.transcribe(str(video), word_timestamps=True, vad_filter=True)
    segs, palavras = [], []
    for s in segmentos:
        segs.append({"i": round(s.start, 2), "f": round(s.end, 2), "texto": s.text.strip()})
        for w in s.words or []:
            palavras.append({"i": round(w.start, 2), "f": round(w.end, 2), "p": w.word.strip()})
    destino.write_text(
        json.dumps({"idioma": info.language, "segmentos": segs, "palavras": palavras}, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("transcritor: %s — %d segmentos, idioma %s", video.name, len(segs), info.language)
    return destino
