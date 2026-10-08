"""Linha de comando.

    python -m cortes configurar              # primeira vez: chave da Anthropic e canais
    python -m cortes auth                    # autoriza o canal do YouTube (uma vez)
    python -m cortes rodar                   # uma rodada comandada pelo Gerente
    python -m cortes rodar --sem-publicar    # tudo menos o upload
    python -m cortes rodar --sem-gerente     # só Planejador + Executor, sem o Claude comandando
    python -m cortes daemon                  # roda para sempre, a cada N minutos
    python -m cortes status                  # fila, problemas e últimos Shorts
    python -m cortes tarefas                 # tarefas abertas e o que precisa de humano
    python -m cortes reabrir 12              # depois de resolver um problema, reabre a tarefa 12
    python -m cortes cancelar 12             # desiste da tarefa 12 (e do vídeo/corte dela)
    python -m cortes retomar-publicacoes     # tira a pausa das publicações
"""
from __future__ import annotations

import argparse
import logging
import time

from dotenv import load_dotenv

from . import config, pipeline, youtube
from .agentes import executor
from .db import agora_iso


def _imprimir_status(ctx) -> None:
    b = ctx.banco
    print(f"vídeos:  {b.contagem('videos') or 'nenhum'}")
    print(f"cortes:  {b.contagem('cortes') or 'nenhum'}")
    print(f"tarefas: {b.contagem('tarefas') or 'nenhuma'}")
    if b.ajuste("publicacao_pausada") == "1":
        print(f"\n⏸  PUBLICAÇÕES PAUSADAS: {b.ajuste('pausa_motivo')}")
    escaladas = b.tarefas_com_status("escalada")
    if escaladas:
        print("\n⚠️  Precisa de você:")
        for t in escaladas:
            print(f"  #{t['id']} {t['tipo']} {t['alvo']}: {t['diagnostico']}")
    publicados = b.con.execute(
        "SELECT titulo, youtube_id, publicar_em FROM cortes WHERE status='publicado' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    if publicados:
        print("\nÚltimos Shorts:")
        for c in publicados:
            print(f"  https://youtube.com/shorts/{c['youtube_id']}  {c['titulo']}  {c['publicar_em'] or ''}")


def _imprimir_tarefas(ctx) -> None:
    linhas = ctx.banco.tarefas_com_status("pendente", "escalada", limite=100)
    if not linhas:
        print("Nenhuma tarefa aberta.")
    for t in linhas:
        print(f"#{t['id']:<4} {t['status']:<9} {t['tipo']:<11} {t['alvo']:<12} tentativas={t['tentativas']} "
              f"executar_após={t['executar_apos']}")
        if t["diagnostico"]:
            print(f"      {t['diagnostico']}")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="cortes")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("configurar")
    sub.add_parser("auth")
    r = sub.add_parser("rodar")
    r.add_argument("--sem-publicar", action="store_true")
    r.add_argument("--sem-gerente", action="store_true")
    sub.add_parser("daemon")
    sub.add_parser("status")
    sub.add_parser("tarefas")
    for nome in ("reabrir", "cancelar"):
        sub.add_parser(nome).add_argument("tarefa_id", type=int)
    sub.add_parser("retomar-publicacoes")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for barulhento in ("googleapiclient", "httpx", "httpx2", "faster_whisper"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)

    if args.comando == "configurar":
        from . import configurar

        configurar.rodar()
        return
    if args.comando == "auth":
        youtube.autenticar_interativo()
        return

    cfg = config.carregar(args.config)
    ctx = pipeline.contexto(cfg)

    if args.comando == "rodar":
        print(pipeline.rodar(cfg, publicar=not args.sem_publicar, usar_gerente=False if args.sem_gerente else None))
    elif args.comando == "daemon":
        while True:
            try:
                print(pipeline.rodar(cfg))
            except Exception:  # noqa: BLE001 — o daemon tenta de novo na próxima rodada
                logging.exception("rodada falhou")
            logging.info("próxima rodada em %d min", cfg.orquestrador.intervalo_minutos)
            time.sleep(cfg.orquestrador.intervalo_minutos * 60)
    elif args.comando == "status":
        _imprimir_status(ctx)
    elif args.comando == "tarefas":
        _imprimir_tarefas(ctx)
    elif args.comando == "reabrir":
        t = ctx.banco.tarefa(args.tarefa_id)
        if not t or t["status"] not in ("pendente", "escalada"):
            raise SystemExit(f"Tarefa #{args.tarefa_id} não está aberta.")
        ctx.banco.atualizar_tarefa(t["id"], status="pendente", tentativas=0, executar_apos=agora_iso(),
                                   diagnostico="Reaberta manualmente")
        print(f"Tarefa #{t['id']} reaberta. Ela roda na próxima rodada.")
    elif args.comando == "cancelar":
        t = ctx.banco.tarefa(args.tarefa_id)
        if not t or t["status"] not in ("pendente", "escalada"):
            raise SystemExit(f"Tarefa #{args.tarefa_id} não está aberta.")
        ctx.banco.atualizar_tarefa(t["id"], status="cancelada", diagnostico="Cancelada manualmente")
        if t["alvo"]:
            executor._descartar_item(ctx, t, "cancelado manualmente")
        print(f"Tarefa #{t['id']} cancelada.")
    elif args.comando == "retomar-publicacoes":
        ctx.banco.definir_ajuste("publicacao_pausada", "0")
        print("Publicações retomadas.")


if __name__ == "__main__":
    main()
