"""Agente Capista: cria a foto de capa (thumbnail) de cada Short.

1. Separa alguns frames do trecho no vídeo original.
2. O Claude olha os frames, escolhe o melhor (rosto expressivo, nítido, olhos abertos),
   indica onde está o assunto principal e escreve o texto da capa.
3. Monta a capa vertical 1080x1920 com o texto grande e palavras em destaque.
"""
from __future__ import annotations

import base64
import io
import logging
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .. import llm
from ..config import Capa

log = logging.getLogger(__name__)

LARGURA, ALTURA = 1080, 1920
LIMITE_BYTES = 2 * 1024 * 1024  # limite do YouTube para thumbnails

ESQUEMA = {
    "type": "object",
    "properties": {
        "frame": {"type": "integer", "description": "número do frame escolhido"},
        "foco_x": {"type": "number", "description": "posição horizontal do assunto principal, de 0 (esquerda) a 1 (direita)"},
        "texto": {"type": "string", "description": "texto da capa, 2 a 5 palavras"},
        "destaques": {"type": "array", "items": {"type": "string"}, "description": "1 ou 2 palavras do texto para pintar de amarelo"},
        "motivo": {"type": "string"},
    },
    "required": ["frame", "foco_x", "texto", "destaques", "motivo"],
    "additionalProperties": False,
}

SISTEMA = """Você é o Agente Capista de uma operação de cortes para YouTube Shorts. Sua função é escolher
a imagem e o texto da capa que façam a pessoa parar de rolar o feed.

Para o frame, prefira: rosto em primeiro plano com expressão forte (surpresa, riso, indignação, concentração),
olhos abertos, boca não borrada, imagem nítida, sem texto ou legenda já na tela. Evite frames de transição,
pessoas de costas, olhos fechados e telas vazias.

Para o texto: 2 a 5 palavras, no idioma do vídeo, que criem curiosidade sem repetir o título nem mentir
sobre o conteúdo. Escolha 1 ou 2 palavras de impacto para destacar."""


def _frame(video: Path, tempo: float, largura: int | None = None) -> Image.Image:
    filtro = ["-vf", f"scale={largura}:-2"] if largura else []
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{tempo:.2f}", "-i", str(video), "-frames:v", "1", *filtro,
         "-f", "image2pipe", "-vcodec", "png", "-"],
        check=True, capture_output=True,
    )
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def tempos_candidatos(inicio: float, fim: float, n: int) -> list[float]:
    margem = min(0.5, (fim - inicio) / 4)
    a, b = inicio + margem, fim - margem
    if n <= 1:
        return [round((a + b) / 2, 2)]
    return [round(a + k * (b - a) / (n - 1), 2) for k in range(n)]


def _jpeg_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _fonte(cfg: Capa, tamanho: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(cfg.fonte_arquivo, tamanho)
    except OSError:
        log.warning("capista: fonte %s não encontrada, usando a padrão", cfg.fonte_arquivo)
        return ImageFont.load_default(tamanho)


def _quebrar(palavras: list[str], fonte, largura_max: int, draw: ImageDraw.ImageDraw) -> list[list[str]]:
    linhas: list[list[str]] = [[]]
    for p in palavras:
        teste = " ".join(linhas[-1] + [p])
        if linhas[-1] and draw.textlength(teste, font=fonte) > largura_max:
            linhas.append([p])
        else:
            linhas[-1].append(p)
    return linhas


def _normalizar(p: str) -> str:
    return "".join(ch for ch in p.upper() if ch.isalnum())


def montar_capa(frame: Image.Image, foco_x: float, texto: str, destaques: list[str], cfg: Capa) -> Image.Image:
    # recorte vertical centrado no assunto principal
    escala = max(LARGURA / frame.width, ALTURA / frame.height)
    img = frame.resize((round(frame.width * escala), round(frame.height * escala)), Image.LANCZOS)
    centro = min(max(foco_x, 0.0), 1.0) * img.width
    esq = int(min(max(centro - LARGURA / 2, 0), img.width - LARGURA))
    topo = (img.height - ALTURA) // 2
    img = img.crop((esq, topo, esq + LARGURA, topo + ALTURA))
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.2)

    # degradê escuro na parte de baixo para o texto aparecer
    sombra = Image.new("L", (1, ALTURA))
    inicio_sombra = int(ALTURA * 0.45)
    for y in range(ALTURA):
        sombra.putpixel((0, y), 0 if y < inicio_sombra else int(200 * (y - inicio_sombra) / (ALTURA - inicio_sombra)))
    img = Image.composite(Image.new("RGB", img.size, "black"), img, sombra.resize(img.size))

    draw = ImageDraw.Draw(img)
    palavras = texto.upper().split()
    alvo = {_normalizar(d) for d in destaques}
    tamanho = 170
    while True:
        fonte = _fonte(cfg, tamanho)
        linhas = _quebrar(palavras, fonte, LARGURA - 120, draw)
        if len(linhas) <= 3 or tamanho <= 70:
            break
        tamanho -= 10

    altura_linha = int(tamanho * 1.1)
    y = int(ALTURA * 0.68) - altura_linha * len(linhas) // 2
    espaco = draw.textlength(" ", font=fonte)
    contorno = max(tamanho // 14, 4)
    for linha in linhas:
        larguras = [draw.textlength(p, font=fonte) for p in linha]
        x = (LARGURA - sum(larguras) - espaco * (len(linha) - 1)) / 2
        for p, w in zip(linha, larguras):
            cor = cfg.cor_destaque if _normalizar(p) in alvo else "white"
            draw.text((x, y), p, font=fonte, fill=cor, stroke_width=contorno, stroke_fill="black")
            x += w + espaco
        y += altura_linha
    return img


def salvar_jpeg(img: Image.Image, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    for qualidade in (92, 85, 75, 65):
        img.save(destino, "JPEG", quality=qualidade, optimize=True)
        if destino.stat().st_size <= LIMITE_BYTES:
            break
    return destino


def criar_capa(video: Path, corte: dict, destino: Path, cfg: Capa) -> Path:
    tempos = tempos_candidatos(corte["inicio"], corte["fim"], cfg.frames_candidatos)
    conteudo: list[dict] = []
    for k, t in enumerate(tempos):
        conteudo.append({"type": "text", "text": f"Frame {k}:"})
        conteudo.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(_frame(video, t, 640))}})
    conteudo.append({
        "type": "text",
        "text": f"Título do Short: {corte['titulo']}\nGancho falado no início: {corte.get('gancho', '')}\n"
                "Escolha o frame e escreva o texto da capa.",
    })
    resp = llm.perguntar_json(SISTEMA, conteudo, ESQUEMA, esforco="low")

    indice = min(max(resp["frame"], 0), len(tempos) - 1)
    frame = _frame(video, tempos[indice])  # resolução original para a capa final
    capa = montar_capa(frame, resp["foco_x"], resp["texto"], resp["destaques"], cfg)
    salvar_jpeg(capa, destino)
    log.info("capista: %s — frame %d, \"%s\" (%s)", destino.name, indice, resp["texto"], resp["motivo"])
    return destino
