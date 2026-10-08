"""Trabalhos: uma função por tipo de tarefa, cada uma chamando o agente especialista certo.

Cada trabalho age sobre um único item (um vídeo ou um corte) e só muda o status dele quando dá certo.
Se der erro, a exceção sobe para o Executor, que decide o que fazer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agentes import baixador, cacador, capista, curador, editor, publicador, revisor, transcritor
from .config import Config
from .db import Banco

log = logging.getLogger(__name__)


class TentarDepois(Exception):
    """O trabalho não pode ser feito agora, mas não é um erro (ex.: limite diário de publicações)."""

    def __init__(self, minutos: int, motivo: str):
        super().__init__(motivo)
        self.minutos = minutos


@dataclass
class Contexto:
    cfg: Config
    banco: Banco
    publicar: bool = True
    _yt: object | None = field(default=None, repr=False)

    @property
    def yt(self):
        if self._yt is None:
            from .youtube import servico

            self._yt = servico()
        return self._yt


def corte_dict(linha) -> dict:
    d = dict(linha)
    d["hashtags"] = json.loads(d["hashtags"] or "[]")
    return d


def cacar(ctx: Contexto, alvo: str) -> str:
    escolhidos = cacador.rodar(ctx.cfg, ctx.banco, ctx.yt)
    return f"{len(escolhidos)} vídeo(s) novo(s): {', '.join(escolhidos) or 'nenhum'}"


def baixar(ctx: Contexto, video_id: str) -> str:
    arquivo = baixador.baixar(video_id, ctx.cfg.pasta_dados / "originais")
    ctx.banco.atualizar_video(video_id, status="baixado", arquivo=str(arquivo))
    return f"baixado em {arquivo}"


def transcrever(ctx: Contexto, video_id: str) -> str:
    v = ctx.banco.video(video_id)
    t = transcritor.transcrever(Path(v["arquivo"]), ctx.cfg.transcricao)
    ctx.banco.atualizar_video(video_id, status="transcrito", transcricao=str(t))
    return f"transcrição em {t}"


def curar(ctx: Contexto, video_id: str) -> str:
    v = ctx.banco.video(video_id)
    cortes = curador.curar(Path(v["transcricao"]), v["titulo"], v["duracao_seg"], ctx.cfg.curador)
    for c in cortes:
        ctx.banco.inserir_corte(
            video_id, inicio=c["inicio"], fim=c["fim"], titulo=c["titulo"], descricao=c["descricao"],
            hashtags=c["hashtags"], gancho=c["gancho"], nota=c["nota"],
        )
    ctx.banco.atualizar_video(video_id, status="curado" if cortes else "descartado")
    if not cortes:
        _apagar_original(ctx, video_id)
    return f"{len(cortes)} corte(s) aprovado(s) pelo curador"


def editar(ctx: Contexto, corte_id: str) -> str:
    """Revisor confere, Editor monta o vídeo e Capista faz a capa."""
    c = corte_dict(ctx.banco.corte(int(corte_id)))
    v = ctx.banco.video(c["video_id"])
    transcricao = Path(v["transcricao"])

    r = revisor.revisar(transcricao, c)
    if not r["aprovado"]:
        ctx.banco.atualizar_corte(c["id"], status="reprovado", erro="; ".join(r["problemas"]))
        _apagar_original_se_terminou(ctx, c["video_id"])
        return "reprovado pelo revisor: " + "; ".join(r["problemas"])

    c.update(titulo=r["titulo"], descricao=r["descricao"], hashtags=r["hashtags"])
    saida = ctx.cfg.pasta_dados / "shorts" / f"{c['video_id']}_{c['id']}.mp4"
    editor.editar(Path(v["arquivo"]), transcricao, c, saida, ctx.cfg.editor)

    capa = None
    if ctx.cfg.capa.ativo:
        try:
            capa = str(capista.criar_capa(Path(v["arquivo"]), c, saida.with_suffix(".jpg"), ctx.cfg.capa))
        except Exception:  # noqa: BLE001 — sem capa o Short ainda pode ser publicado
            log.exception("falha ao criar capa do corte %s", c["id"])

    ctx.banco.atualizar_corte(
        c["id"], status="editado", arquivo=str(saida), titulo=c["titulo"], descricao=c["descricao"],
        hashtags=json.dumps(c["hashtags"], ensure_ascii=False), capa=capa,
    )
    _apagar_original_se_terminou(ctx, c["video_id"])
    return f"editado em {saida}" + ("" if capa else " (sem capa)")


def publicar(ctx: Contexto, corte_id: str) -> str:
    p = ctx.cfg.publicador
    if ctx.banco.ajuste("publicacao_pausada") == "1":
        raise TentarDepois(60, "publicações pausadas: " + (ctx.banco.ajuste("pausa_motivo") or ""))
    agora = datetime.now(timezone.utc)
    if ctx.banco.publicados_desde((agora - timedelta(hours=24)).isoformat()) >= p.max_postagens_por_dia:
        raise TentarDepois(120, f"limite de {p.max_postagens_por_dia} publicações em 24h atingido")

    c = corte_dict(ctx.banco.corte(int(corte_id)))
    v = dict(ctx.banco.video(c["video_id"]))
    meta = publicador.montar_metadados(c, v, p)
    # só agenda quando o destino é público; em modo de teste (private/unlisted) sobe direto
    quando = publicador.proximo_horario(ctx.banco.ultimo_agendamento(), p) if p.privacidade == "public" else None
    yt_id = publicador.publicar(ctx.yt, Path(c["arquivo"]), meta, quando)
    ctx.banco.atualizar_corte(
        c["id"], status="publicado", youtube_id=yt_id, publicado_em=agora.isoformat(),
        publicar_em=quando.isoformat() if quando else None,
    )
    capa_ok = bool(c["capa"]) and publicador.enviar_capa(ctx.yt, yt_id, Path(c["capa"]))
    return f"https://youtube.com/shorts/{yt_id}" + ("" if capa_ok else " (sem capa)")


def _apagar_original(ctx: Contexto, video_id: str) -> None:
    v = ctx.banco.video(video_id)
    if v["arquivo"]:
        Path(v["arquivo"]).unlink(missing_ok=True)


def _apagar_original_se_terminou(ctx: Contexto, video_id: str) -> None:
    """Depois que todos os cortes do vídeo saíram da fila de edição, o original não é mais necessário."""
    if not ctx.banco.con.execute(
        "SELECT 1 FROM cortes WHERE video_id=? AND status='curado'", (video_id,)
    ).fetchone():
        _apagar_original(ctx, video_id)


TRABALHOS = {
    "cacar": cacar,
    "baixar": baixar,
    "transcrever": transcrever,
    "curar": curar,
    "editar": editar,
    "publicar": publicar,
}
