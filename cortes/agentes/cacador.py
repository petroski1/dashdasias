"""Agente Caçador: encontra vídeos em alta nas fontes autorizadas e escolhe os melhores para cortar."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import llm
from ..config import Config
from ..db import Banco
from ..youtube import duracao_iso_para_seg

log = logging.getLogger(__name__)


@dataclass
class Candidato:
    id: str
    titulo: str
    descricao: str
    canal: str
    canal_id: str
    publicado_em: datetime
    visualizacoes: int
    curtidas: int
    comentarios: int
    duracao_seg: int
    licenca: str
    nota: float = 0.0


def pontuar(c: Candidato, agora: datetime | None = None) -> float:
    """Velocidade de views (views/hora, amortecida) com bônus por engajamento."""
    agora = agora or datetime.now(timezone.utc)
    horas = max((agora - c.publicado_em).total_seconds() / 3600, 1)
    velocidade = c.visualizacoes / horas**0.8
    engajamento = (c.curtidas + 3 * c.comentarios) / max(c.visualizacoes, 1)
    return math.log10(velocidade + 1) * (1 + min(engajamento * 10, 1))


def _detalhes(yt, ids: list[str]) -> list[Candidato]:
    saida = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids[i : i + 50])).execute()
        for v in r.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            if sn.get("liveBroadcastContent") != "none":
                continue
            saida.append(
                Candidato(
                    id=v["id"],
                    titulo=sn["title"],
                    descricao=sn.get("description", "")[:500],
                    canal=sn["channelTitle"],
                    canal_id=sn["channelId"],
                    publicado_em=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
                    visualizacoes=int(st.get("viewCount", 0)),
                    curtidas=int(st.get("likeCount", 0)),
                    comentarios=int(st.get("commentCount", 0)),
                    duracao_seg=duracao_iso_para_seg(v["contentDetails"]["duration"]),
                    licenca=v["status"].get("license", "youtube"),
                )
            )
    return saida


def _ids_dos_canais(yt, canais: list[str]) -> list[str]:
    ids = []
    for canal in canais:
        r = yt.channels().list(part="contentDetails", id=canal).execute()
        if not r.get("items"):
            log.warning("canal %s não encontrado", canal)
            continue
        uploads = r["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50).execute()
        ids += [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
    return ids


def _ids_creative_commons(yt, buscas: list[str], desde: datetime) -> list[str]:
    ids = []
    for termo in buscas:  # cada busca custa 100 unidades da cota
        r = yt.search().list(
            part="id", q=termo, type="video", videoLicense="creativeCommon", order="viewCount",
            publishedAfter=desde.isoformat().replace("+00:00", "Z"), maxResults=25,
        ).execute()
        ids += [it["id"]["videoId"] for it in r.get("items", [])]
    return ids


ESQUEMA_ESCOLHA = {
    "type": "object",
    "properties": {
        "escolhidos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "potencial": {"type": "number", "description": "0 a 10"},
                    "motivo": {"type": "string"},
                },
                "required": ["id", "potencial", "motivo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["escolhidos"],
    "additionalProperties": False,
}

SISTEMA = """Você é o Agente Caçador de uma operação de cortes para YouTube Shorts.
Recebe uma lista de vídeos longos em alta e decide quais têm mais potencial de render cortes curtos virais:
momentos com opinião forte, histórias, revelações, humor, conflito, dicas práticas ou frases de efeito.
Vídeos de música, trailers, compilações já editadas ou conteúdo muito visual sem fala rendem mal.
Devolva os vídeos escolhidos ordenados do maior para o menor potencial."""


def rodar(cfg: Config, banco: Banco, yt) -> list[str]:
    c = cfg.cacador
    agora = datetime.now(timezone.utc)
    desde = agora - timedelta(days=c.dias_maximos)

    ids = _ids_dos_canais(yt, cfg.fontes.canais_autorizados)
    ids += _ids_creative_commons(yt, cfg.fontes.buscas_creative_commons, desde)
    ids = [i for i in dict.fromkeys(ids) if not banco.video_existe(i)]
    if not ids:
        log.info("caçador: nenhum vídeo novo")
        return []

    autorizados = set(cfg.fontes.canais_autorizados)
    candidatos = [
        v for v in _detalhes(yt, ids)
        if v.publicado_em >= desde
        and v.visualizacoes >= c.visualizacoes_minimas
        and c.duracao_minima_seg <= v.duracao_seg <= c.duracao_maxima_seg
        # trava de direitos: só canal autorizado ou licença Creative Commons
        and (v.canal_id in autorizados or v.licenca == "creativeCommon")
    ]
    for v in candidatos:
        v.nota = pontuar(v, agora)
    candidatos.sort(key=lambda v: v.nota, reverse=True)
    candidatos = candidatos[: max(c.videos_por_rodada * 4, 8)]
    if not candidatos:
        log.info("caçador: nada passou nos filtros")
        return []

    lista = "\n".join(
        f"- id={v.id} | {v.titulo} | canal={v.canal} | {v.visualizacoes:,} views | "
        f"{v.duracao_seg // 60} min | nota_tendência={v.nota:.2f}\n  {v.descricao[:200]!r}"
        for v in candidatos
    )
    resp = llm.perguntar_json(
        SISTEMA,
        f"Escolha até {c.videos_por_rodada} vídeos para cortar hoje.\n\n{lista}",
        ESQUEMA_ESCOLHA,
        esforco="low",
    )
    por_id = {v.id: v for v in candidatos}
    escolhidos = [e for e in resp["escolhidos"] if e["id"] in por_id][: c.videos_por_rodada]

    for e in escolhidos:
        v = por_id[e["id"]]
        banco.inserir_video(
            id=v.id, titulo=v.titulo, canal=v.canal, canal_id=v.canal_id, visualizacoes=v.visualizacoes,
            duracao_seg=v.duracao_seg, publicado_em=v.publicado_em.isoformat(), nota=e["potencial"],
        )
        log.info("caçador: escolhido %s (%s) — %s", v.id, v.titulo, e["motivo"])
    return [e["id"] for e in escolhidos]
