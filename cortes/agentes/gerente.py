"""Agente Gerente (orquestrador): comanda a rodada inteira.

O Claude recebe ferramentas para ver o painel, mandar o Planejador criar tarefas, mandar o Executor
trabalhar, resolver problemas (reabrir/cancelar tarefas) e pausar publicações. No fim escreve um
relatório da rodada, salvo em dados/relatorios/.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from anthropic import beta_tool

from .. import llm
from ..db import agora_iso
from ..trabalhos import Contexto
from . import executor, planejador

log = logging.getLogger(__name__)

SISTEMA = """Você é o Agente Gerente de uma operação automática que transforma vídeos do YouTube em Shorts.
Sua equipe:
- Caçador (acha vídeos em alta nas fontes autorizadas), Baixador, Transcritor, Curador (escolhe os trechos),
  Revisor (confere título e políticas), Editor (monta o vídeo vertical), Capista (faz a capa) e Publicador.
- Planejador: transforma o estado de cada vídeo/corte em tarefas.
- Executor: executa as tarefas e trata erros (tenta de novo, adia, descarta o item ou escala para um humano).

Em cada rodada:
1. Veja o painel.
2. Cuide dos problemas: reabra tarefas cuja causa era passageira e já deve ter passado; cancele itens sem
   salvação. Não reabra tarefas que dependem de uma ação humana ainda não feita (login, chave, disco cheio).
3. Se houver sinal de problema sério com o canal — várias publicações falhando seguidas, login do YouTube
   expirado, muitos cortes reprovados pelo revisor — pause as publicações e explique por quê.
   Retome se a causa da pausa já foi resolvida.
4. Mande o Planejador criar as tarefas e o Executor trabalhar. Repita enquanto houver tarefas prontas e
   progresso.
5. Termine com um relatório curto em português, para o dono do canal ler no celular: o que foi publicado
   (com links), o que está na fila, e — em destaque — o que precisa de ação humana e como fazer.

Você não pode mudar as fontes autorizadas, os limites diários nem a privacidade dos vídeos; essas decisões
são do dono do canal. Se achar que algo deveria mudar, sugira no relatório."""


def _ferramentas(ctx: Contexto) -> list:
    banco = ctx.banco

    @beta_tool
    def ver_painel() -> str:
        """Mostra a situação atual: fila de vídeos e cortes, tarefas, publicações nas últimas 24h e pausas."""
        agora = datetime.now(timezone.utc)
        publicados = banco.publicados_desde((agora - timedelta(hours=24)).isoformat())
        linhas = [
            f"Agora: {agora_iso()}",
            f"Vídeos por status: {banco.contagem('videos') or 'nenhum'}",
            f"Cortes por status: {banco.contagem('cortes') or 'nenhum'}",
            f"Tarefas por status: {banco.contagem('tarefas') or 'nenhuma'}",
            f"Publicados nas últimas 24h: {publicados} de {ctx.cfg.publicador.max_postagens_por_dia} "
            f"(privacidade: {ctx.cfg.publicador.privacidade})",
            f"Publicação ativada nesta rodada: {'sim' if ctx.publicar else 'não (modo de teste)'}",
        ]
        if banco.ajuste("publicacao_pausada") == "1":
            linhas.append(f"PUBLICAÇÕES PAUSADAS: {banco.ajuste('pausa_motivo')}")
        ultimos = banco.con.execute(
            "SELECT titulo, youtube_id FROM cortes WHERE status='publicado' ORDER BY publicado_em DESC LIMIT 5"
        ).fetchall()
        if ultimos:
            linhas.append("Últimos publicados:\n" + "\n".join(
                f"  - {c['titulo']} https://youtube.com/shorts/{c['youtube_id']}" for c in ultimos))
        return "\n".join(linhas)

    @beta_tool
    def listar_problemas(limite: int = 15) -> str:
        """Lista tarefas escaladas para humano, descartadas ou com erro recente, com o diagnóstico do Executor.

        Args:
            limite: quantas tarefas mostrar no máximo.
        """
        linhas = []
        for t in banco.tarefas_com_status("escalada", "falhou", limite=limite):
            linhas.append(f"#{t['id']} {t['tipo']} {t['alvo']} [{t['status']}, {t['tentativas']} tentativas, "
                          f"{t['atualizado_em']}]: {t['diagnostico']}")
        for t in banco.con.execute(
            "SELECT * FROM tarefas WHERE status='pendente' AND tentativas > 0 ORDER BY atualizado_em DESC LIMIT ?",
            (limite,),
        ):
            linhas.append(f"#{t['id']} {t['tipo']} {t['alvo']} [pendente, {t['tentativas']} tentativas, "
                          f"próxima às {t['executar_apos']}]: {t['diagnostico']}")
        return "\n".join(linhas) or "Nenhum problema registrado."

    @beta_tool
    def planejar_tarefas() -> str:
        """Pede ao Planejador para criar as tarefas que faltam com base no estado atual."""
        criadas = planejador.planejar(ctx)
        return ("Tarefas criadas: " + ", ".join(f"{t} {a}".strip() for t, a in criadas)) if criadas else "Nenhuma tarefa nova."

    @beta_tool
    def executar_tarefas(tipos: list[str] | None = None, limite: int = 20) -> str:
        """Manda o Executor trabalhar nas tarefas prontas. Ele encadeia as etapas sozinho
        (baixar → transcrever → curar → editar → publicar) e trata os erros.

        Args:
            tipos: só executar estes tipos (cacar, baixar, transcrever, curar, editar, publicar). Vazio = todos.
            limite: máximo de tarefas nesta chamada.
        """
        return executor.executar(ctx, tipos=tipos or None, limite=limite).texto()

    @beta_tool
    def reabrir_tarefa(tarefa_id: int, motivo: str) -> str:
        """Volta uma tarefa escalada ou adiada para a fila, zerando as tentativas.

        Args:
            tarefa_id: número da tarefa.
            motivo: por que vale tentar de novo.
        """
        t = banco.tarefa(tarefa_id)
        if not t or t["status"] in ("feita", "cancelada", "falhou"):
            return f"Tarefa #{tarefa_id} não pode ser reaberta."
        banco.atualizar_tarefa(tarefa_id, status="pendente", tentativas=0, executar_apos=agora_iso(),
                               diagnostico=f"Reaberta pelo gerente: {motivo}")
        return f"Tarefa #{tarefa_id} reaberta."

    @beta_tool
    def cancelar_tarefa(tarefa_id: int, motivo: str) -> str:
        """Cancela uma tarefa e descarta o vídeo ou corte dela, para a fila seguir.

        Args:
            tarefa_id: número da tarefa.
            motivo: por que o item não tem salvação.
        """
        t = banco.tarefa(tarefa_id)
        if not t or t["status"] not in ("pendente", "escalada"):
            return f"Tarefa #{tarefa_id} não está aberta."
        banco.atualizar_tarefa(tarefa_id, status="cancelada", diagnostico=f"Cancelada pelo gerente: {motivo}")
        if t["alvo"]:
            executor._descartar_item(ctx, t, f"cancelado pelo gerente: {motivo}")
        return f"Tarefa #{tarefa_id} cancelada."

    @beta_tool
    def pausar_publicacoes(motivo: str) -> str:
        """Pausa todas as publicações no YouTube (o resto da fila continua andando).

        Args:
            motivo: explicação para o dono do canal.
        """
        banco.definir_ajuste("publicacao_pausada", "1")
        banco.definir_ajuste("pausa_motivo", motivo)
        return "Publicações pausadas."

    @beta_tool
    def retomar_publicacoes() -> str:
        """Retoma as publicações depois de uma pausa."""
        banco.definir_ajuste("publicacao_pausada", "0")
        banco.definir_ajuste("pausa_motivo", None)
        return "Publicações retomadas."

    return [ver_painel, listar_problemas, planejar_tarefas, executar_tarefas, reabrir_tarefa,
            cancelar_tarefa, pausar_publicacoes, retomar_publicacoes]


def rodar(ctx: Contexto) -> str:
    runner = llm.cliente().beta.messages.tool_runner(
        model=llm.MODELO,
        max_tokens=16000,
        system=SISTEMA,
        tools=_ferramentas(ctx),
        messages=[{"role": "user", "content": "Comece a rodada."}],
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=25,
    )
    ultima = None
    for mensagem in runner:
        ultima = mensagem
        for bloco in mensagem.content:
            if bloco.type == "tool_use":
                log.info("gerente: %s(%s)", bloco.name, bloco.input or "")
    if ultima is None or ultima.stop_reason == "refusal":
        raise llm.RespostaRecusada("o gerente não concluiu a rodada")
    relatorio = "\n".join(b.text for b in ultima.content if b.type == "text").strip()
    if not relatorio:
        relatorio = "O gerente atingiu o limite de passos sem escrever o relatório. Veja: python -m cortes status"

    pasta = ctx.cfg.pasta_dados / "relatorios"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"{datetime.now():%Y-%m-%d_%H%M}.md"
    arquivo.write_text(relatorio + "\n", encoding="utf-8")
    log.info("gerente: relatório salvo em %s", arquivo)
    return relatorio
