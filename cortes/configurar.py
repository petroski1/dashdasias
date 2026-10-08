"""Assistente de primeira configuração: `python -m cortes configurar`.

Pergunta a chave da Anthropic sem mostrar na tela, testa se ela funciona e grava no .env;
cria o config.yaml a partir do exemplo com os canais autorizados.
"""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

ENV = Path(".env")
CONFIG = Path("config.yaml")
EXEMPLO = Path("config.example.yaml")
MARCADOR_CANAL = "    - UCxxxxxxxxxxxxxxxxxxxxxx\n"


def testar_chave(chave: str) -> str | None:
    """Devolve None se a chave funciona, ou a mensagem de erro."""
    import anthropic

    try:
        anthropic.Anthropic(api_key=chave).models.list(limit=1)
        return None
    except anthropic.AuthenticationError:
        return "a Anthropic recusou essa chave (confira se copiou inteira)"
    except anthropic.APIConnectionError:
        return "sem conexão com a Anthropic (verifique a internet)"
    except anthropic.APIStatusError as e:
        return f"erro {e.status_code} da Anthropic"


def gravar_env(chave: str, caminho: Path = ENV) -> None:
    linhas = caminho.read_text(encoding="utf-8").splitlines() if caminho.exists() else []
    linhas = [l for l in linhas if not l.startswith("ANTHROPIC_API_KEY=")]
    linhas.insert(0, f"ANTHROPIC_API_KEY={chave}")
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    if os.name != "nt":
        caminho.chmod(0o600)  # só o seu usuário consegue ler


def gravar_canais(canais: list[str], caminho: Path = CONFIG, exemplo: Path = EXEMPLO) -> None:
    texto = (caminho if caminho.exists() else exemplo).read_text(encoding="utf-8")
    if canais and MARCADOR_CANAL in texto:
        texto = texto.replace(MARCADOR_CANAL, "".join(f"    - {c}\n" for c in canais))
    caminho.write_text(texto, encoding="utf-8")


def _perguntar_chave() -> None:
    print("\n1) Chave da API da Anthropic (crie em https://console.anthropic.com/settings/keys)")
    print("   Cole e aperte Enter. Por segurança, nada vai aparecer enquanto você cola.")
    while True:
        chave = getpass.getpass("   Chave: ").strip()
        if not chave:
            print("   Pulando — a chave atual (se houver) continua.")
            return
        if not chave.startswith("sk-ant-"):
            print("   Essa não parece uma chave da Anthropic (começa com sk-ant-). Tente de novo.")
            continue
        print("   Testando...")
        erro = testar_chave(chave)
        if erro:
            print(f"   ✗ {erro}. Tente de novo ou aperte Enter para pular.")
            continue
        gravar_env(chave)
        print(f"   ✓ Chave funcionando e salva em {ENV.resolve()}")
        return


def _perguntar_canais() -> None:
    print("\n2) Canais que você tem autorização para cortar")
    print("   IDs começam com UC (no YouTube: canal → Sobre → Compartilhar canal → Copiar ID do canal).")
    print("   Separe vários por vírgula. Enter para deixar para depois.")
    resposta = input("   Canais: ").strip()
    canais = [c for c in re.split(r"[,\s]+", resposta) if c]
    invalidos = [c for c in canais if not re.fullmatch(r"UC[\w-]{22}", c)]
    if invalidos:
        print(f"   Atenção: estes não parecem IDs de canal e foram ignorados: {', '.join(invalidos)}")
        canais = [c for c in canais if c not in invalidos]
    gravar_canais(canais)
    print(f"   ✓ {CONFIG} pronto" + (f" com {len(canais)} canal(is)" if canais else " (edite os canais depois)"))


def rodar() -> None:
    print("Configuração da Fábrica de Cortes")
    _perguntar_chave()
    _perguntar_canais()
    print("\n3) Próximo passo: conectar o YouTube com  python -m cortes auth")
    print("   (precisa do client_secret.json do Google Cloud — veja o README)")
