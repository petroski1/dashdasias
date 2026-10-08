"""Linha de comando.

    python -m cortes auth              # autoriza o canal do YouTube (uma vez)
    python -m cortes rodar             # uma rodada completa: caça, corta, edita e publica
    python -m cortes rodar --sem-publicar
    python -m cortes daemon            # roda para sempre, a cada N minutos
    python -m cortes status            # mostra a fila
"""
from __future__ import annotations

import argparse
import logging
import time

from dotenv import load_dotenv

from . import config, pipeline, youtube
from .db import Banco


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="cortes")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("auth")
    r = sub.add_parser("rodar")
    r.add_argument("--sem-publicar", action="store_true")
    sub.add_parser("daemon")
    sub.add_parser("status")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for barulhento in ("googleapiclient", "httpx", "faster_whisper"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)

    if args.comando == "auth":
        youtube.autenticar_interativo()
        return

    cfg = config.carregar(args.config)

    if args.comando == "rodar":
        pipeline.rodar(cfg, publicar=not args.sem_publicar)
    elif args.comando == "daemon":
        while True:
            try:
                pipeline.rodar(cfg)
            except Exception:  # noqa: BLE001 — o daemon tenta de novo na próxima rodada
                logging.exception("rodada falhou")
            logging.info("próxima rodada em %d min", cfg.orquestrador.intervalo_minutos)
            time.sleep(cfg.orquestrador.intervalo_minutos * 60)
    elif args.comando == "status":
        banco = Banco(cfg.pasta_dados / "cortes.db")
        for tabela in ("videos", "cortes"):
            linhas = banco.con.execute(f"SELECT status, COUNT(*) FROM {tabela} GROUP BY status").fetchall()
            print(f"{tabela}: " + (", ".join(f"{s}={n}" for s, n in linhas) or "vazio"))
        for c in banco.con.execute(
            "SELECT titulo, youtube_id, publicar_em FROM cortes WHERE status='publicado' ORDER BY id DESC LIMIT 10"
        ):
            print(f"  https://youtube.com/shorts/{c['youtube_id']}  {c['titulo']}  {c['publicar_em'] or ''}")


if __name__ == "__main__":
    main()
