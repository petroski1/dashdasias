"""Agente Editor: recorta o trecho, converte para 9:16 (1080x1920), queima legendas animadas e o gancho."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from ..config import Editor

log = logging.getLogger(__name__)

LARGURA, ALTURA = 1080, 1920
BRANCO = "&H00FFFFFF"


def _tempo_ass(seg: float) -> str:
    seg = max(seg, 0)
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _limpar(texto: str) -> str:
    return texto.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").strip()


def gerar_ass(palavras: list[dict], inicio: float, fim: float, gancho: str, cfg: Editor) -> str:
    """Legenda estilo 'karaokê': blocos de N palavras, com a palavra falada em destaque."""
    cabecalho = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {LARGURA}
PlayResY: {ALTURA}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Legenda,{cfg.fonte},86,{BRANCO},{BRANCO},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Gancho,{cfg.fonte},66,{BRANCO},{BRANCO},&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,18,0,8,80,80,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    linhas = []
    duracao = fim - inicio
    if gancho:
        linhas.append(f"Dialogue: 1,{_tempo_ass(0)},{_tempo_ass(min(3.0, duracao))},Gancho,,0,0,0,,{_limpar(gancho).upper()}")

    if cfg.legendas:
        ws = [
            {"i": w["i"] - inicio, "f": w["f"] - inicio, "p": _limpar(w["p"]).upper()}
            for w in palavras
            if w["i"] >= inicio - 0.05 and w["f"] <= fim + 0.05 and _limpar(w["p"])
        ]
        n = max(cfg.palavras_por_legenda, 1)
        for b in range(0, len(ws), n):
            bloco = ws[b : b + n]
            fim_bloco = ws[b + n]["i"] if b + n < len(ws) else bloco[-1]["f"]
            for k, w in enumerate(bloco):
                ate = bloco[k + 1]["i"] if k + 1 < len(bloco) else fim_bloco
                texto = " ".join(
                    f"{{\\c{cfg.cor_destaque}}}{x['p']}{{\\c{BRANCO}}}" if j == k else x["p"]
                    for j, x in enumerate(bloco)
                )
                linhas.append(f"Dialogue: 0,{_tempo_ass(w['i'])},{_tempo_ass(min(ate, duracao))},Legenda,,0,0,0,,{texto}")
    return cabecalho + "\n".join(linhas) + "\n"


def filtro_video(estilo: str, legenda: str | None) -> str:
    if estilo == "recorte":
        base = f"[0:v]scale=-2:{ALTURA},crop={LARGURA}:{ALTURA},setsar=1[v0]"
    else:
        base = (
            f"[0:v]split[a][b];"
            f"[a]scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,crop={LARGURA}:{ALTURA},boxblur=24:4[fundo];"
            f"[b]scale={LARGURA}:-2[frente];"
            f"[fundo][frente]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
        )
    if legenda:
        return base + f";[v0]ass={legenda}[v]"
    return base + ";[v0]null[v]"


def editar(video: Path, transcricao: Path, corte: dict, saida: Path, cfg: Editor) -> Path:
    palavras = json.loads(transcricao.read_text(encoding="utf-8"))["palavras"]
    inicio, fim = corte["inicio"], corte["fim"]
    saida.parent.mkdir(parents=True, exist_ok=True)

    ass = saida.with_suffix(".ass")
    ass.write_text(gerar_ass(palavras, inicio, fim, corte.get("gancho", ""), cfg), encoding="utf-8")

    comando = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{inicio:.2f}", "-i", str(video.resolve()), "-t", f"{fim - inicio:.2f}",
        # roda dentro da pasta da legenda para não precisar escapar o caminho no filtro
        "-filter_complex", filtro_video(cfg.estilo, ass.name),
        "-map", "[v]", "-map", "0:a?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        saida.name,
    ]
    subprocess.run(comando, check=True, cwd=saida.parent)
    verificar(saida)
    log.info("editor: %s (%.1fs)", saida.name, fim - inicio)
    return saida


def verificar(arquivo: Path, max_seg: float = 180) -> dict:
    """Checagem técnica antes de publicar: vertical, com áudio e dentro do limite do Shorts."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(arquivo)],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(r.stdout)
    duracao = float(info["format"]["duration"])
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    tem_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    if video["height"] <= video["width"]:
        raise ValueError(f"{arquivo.name}: vídeo não está na vertical ({video['width']}x{video['height']})")
    if duracao > max_seg:
        raise ValueError(f"{arquivo.name}: {duracao:.0f}s passa do limite de {max_seg:.0f}s")
    if not tem_audio:
        raise ValueError(f"{arquivo.name}: sem áudio")
    return {"duracao": duracao, "largura": video["width"], "altura": video["height"]}
