"""Acesso autenticado à YouTube Data API v3 (OAuth do dono do canal)."""
from __future__ import annotations

import re
from pathlib import Path

ESCOPOS = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CREDENCIAIS_CLIENTE = Path("client_secret.json")
TOKEN = Path("token.json")


def autenticar_interativo() -> None:
    """Abre o navegador para o dono do canal autorizar. Rode uma vez: `python -m cortes auth`."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENCIAIS_CLIENTE.exists():
        raise SystemExit(
            "Baixe o client_secret.json (OAuth 'App para computador') no Google Cloud Console "
            "e salve na raiz do projeto."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENCIAIS_CLIENTE), ESCOPOS)
    cred = flow.run_local_server(port=0, open_browser=True)
    TOKEN.write_text(cred.to_json(), encoding="utf-8")
    print(f"Autorizado. Token salvo em {TOKEN}.")


def servico():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN.exists():
        raise SystemExit("Sem token do YouTube. Rode: python -m cortes auth")
    cred = Credentials.from_authorized_user_file(str(TOKEN), ESCOPOS)
    if not cred.valid and cred.refresh_token:
        cred.refresh(Request())
        TOKEN.write_text(cred.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=cred, cache_discovery=False)


_DURACAO = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def duracao_iso_para_seg(iso: str) -> int:
    """'PT1H2M3S' -> 3723."""
    m = _DURACAO.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s
