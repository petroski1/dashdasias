"""Agente Executor: pega as tarefas prontas, chama o agente certo e cuida dos erros.

Quando algo dá errado, o Claude lê o erro e decide, como alguém do administrativo faria:
tentar de novo agora, tentar mais tarde, desistir daquele item ou chamar um humano.
Erros conhecidos (cota do YouTube, login expirado, chave inválida) são tratados direto, sem IA.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import llm, trabalhos
from ..trabalhos import Contexto, TentarDepois
from . import planejador

log = logging.getLogger(__name__)

ESQUEMA = {
    "type": "object",
    "properties": {
        "acao": {"type": "string", "enum": ["tentar_novamente", "tentar_mais_tarde", "desistir", "escalar"]},
        "espera_minutos": {"type": "integer", "description": "só para tentar_mais_tarde"},
        "diagnostico": {"type": "string", "description": "o que aconteceu, em linguagem simples"},
        "o_que_fazer": {"type": "string", "description": "para escalar: o passo a passo que o humano deve seguir"},
    },
    "required": ["acao", "espera_minutos", "diagnostico", "o_que_fazer"],
    "additionalProperties": False,
}

SISTEMA = """Você é o Agente Executor de uma operação automática de cortes para YouTube Shorts.
Uma tarefa falhou e você decide o que fazer, como um bom funcionário do administrativo:

- tentar_novamente: falha passageira que provavelmente some se repetir já (instabilidade, timeout curto).
- tentar_mais_tarde: algo externo que deve se resolver sozinho com tempo (rede fora, serviço instável,
  limite de requisições). Indique espera_minutos.
- desistir: o problema é deste item específico e não vai mudar (vídeo removido, privado, bloqueado na região,
  arquivo corrompido, conteúdo sem fala). O item é descartado e a fila segue.
- escalar: precisa de uma pessoa (credencial ou login inválido, falta de espaço em disco, programa não
  instalado, erro no código, configuração errada). Explique em o_que_fazer o passo a passo, para alguém
  que não é programador.

Escreva diagnostico e o_que_fazer em português do Brasil, curtos e diretos."""

# erros que não precisam de IA para decidir
REGRAS = [
    (("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"), "adiar", 360,
     "A cota diária da API do YouTube acabou. Ela renova à meia-noite do horário do Pacífico."),
    (("invalid_grant", "RefreshError", "Token has been expired"), "escalar", 0,
     "O login do YouTube expirou. Rode no terminal: python -m cortes auth"),
    (("authentication_error", "AuthenticationError", "invalid x-api-key"), "escalar", 0,
     "A chave da API da Anthropic está inválida. Confira ANTHROPIC_API_KEY no arquivo .env."),
    (("HyperFrames não instalado",), "escalar", 0,
     "O HyperFrames não está instalado. Rode: cd hyperframes && npm install && npm run preparar"),
    (("No space left on device",), "escalar", 0,
     "O disco está cheio. Libere espaço (ex.: apague a pasta dados/originais) e reabra a tarefa."),
    (("Video unavailable", "Private video", "This video has been removed", "not available in your country"),
     "desistir", 0, "O vídeo não está mais disponível para download."),
]


@dataclass
class Resumo:
    feitas: list[str] = field(default_factory=list)
    adiadas: list[str] = field(default_factory=list)
    descartadas: list[str] = field(default_factory=list)
    escaladas: list[str] = field(default_factory=list)

    def texto(self) -> str:
        partes = []
        for nome, itens in (("Feitas", self.feitas), ("Adiadas", self.adiadas),
                            ("Descartadas", self.descartadas), ("Precisam de humano", self.escaladas)):
            if itens:
                partes.append(f"{nome} ({len(itens)}):\n" + "\n".join(f"  - {i}" for i in itens))
        return "\n".join(partes) or "Nenhuma tarefa pronta para executar."


def _rotulo(t) -> str:
    return f"#{t['id']} {t['tipo']}" + (f" {t['alvo']}" if t["alvo"] else "")


def _descricao_item(ctx: Contexto, t) -> str:
    if t["tipo"] in ("baixar", "transcrever", "curar") and t["alvo"]:
        v = ctx.banco.video(t["alvo"])
        return f"vídeo {t['alvo']} \"{v['titulo']}\" ({v['canal']})" if v else ""
    if t["tipo"] in ("editar", "publicar") and t["alvo"]:
        c = ctx.banco.corte(int(t["alvo"]))
        return f"corte {t['alvo']} \"{c['titulo']}\" do vídeo {c['video_id']}" if c else ""
    return ""


def diagnosticar(ctx: Contexto, t, erro: str, tentativas: int) -> dict:
    for gatilhos, acao, minutos, texto in REGRAS:
        if any(g in erro for g in gatilhos):
            return {"acao": "tentar_mais_tarde" if acao == "adiar" else acao, "espera_minutos": minutos,
                    "diagnostico": texto, "o_que_fazer": texto if acao == "escalar" else "", "regra": True}
    pedido = (
        f"Tarefa: {t['tipo']} {_descricao_item(ctx, t)}\n"
        f"Tentativa {tentativas} de {ctx.cfg.gerencia.max_tentativas}\n\n<erro>\n{erro}\n</erro>"
    )
    try:
        return llm.perguntar_json(SISTEMA, pedido, ESQUEMA, esforco="low")
    except Exception as e:  # noqa: BLE001 — se nem o diagnóstico funcionar, adia e tenta depois
        log.warning("executor: não consegui diagnosticar com o Claude (%s)", e)
        return {"acao": "tentar_mais_tarde", "espera_minutos": 30,
                "diagnostico": f"Erro não diagnosticado: {erro.strip().splitlines()[-1][:300]}",
                "o_que_fazer": "Veja o erro completo com: python -m cortes tarefas"}


def _descartar_item(ctx: Contexto, t, motivo: str) -> None:
    if t["tipo"] in ("baixar", "transcrever", "curar"):
        ctx.banco.atualizar_video(t["alvo"], status="erro", erro=motivo)
    elif t["tipo"] in ("editar", "publicar"):
        c = ctx.banco.corte(int(t["alvo"]))
        ctx.banco.atualizar_corte(c["id"], status="erro", erro=motivo)
        trabalhos._apagar_original_se_terminou(ctx, c["video_id"])


def _tratar_erro(ctx: Contexto, t, exc: Exception, resumo: Resumo) -> None:
    rotulo = _rotulo(t)
    if isinstance(exc, TentarDepois):  # não é erro: só não dá para fazer agora
        quando = datetime.now(timezone.utc) + timedelta(minutes=exc.minutos)
        ctx.banco.atualizar_tarefa(t["id"], executar_apos=quando.isoformat(timespec="seconds"), resultado=str(exc))
        resumo.adiadas.append(f"{rotulo}: {exc}")
        return

    tentativas = t["tentativas"] + 1
    erro = "".join(traceback.format_exception(exc))[-4000:]
    d = diagnosticar(ctx, t, erro, tentativas)
    acao = d["acao"]
    if acao in ("tentar_novamente", "tentar_mais_tarde") and tentativas >= ctx.cfg.gerencia.max_tentativas and not d.get("regra"):
        acao = "escalar"
        d["o_que_fazer"] = d["o_que_fazer"] or f"Falhou {tentativas} vezes. Veja o erro e reabra a tarefa quando resolver."
    log.warning("executor: %s falhou (tentativa %d) -> %s: %s", rotulo, tentativas, acao, d["diagnostico"])

    campos = {"tentativas": tentativas, "erro": erro, "diagnostico": d["diagnostico"]}
    if acao == "tentar_novamente":
        ctx.banco.atualizar_tarefa(t["id"], **campos)
        resumo.adiadas.append(f"{rotulo}: vai tentar de novo — {d['diagnostico']}")
    elif acao == "tentar_mais_tarde":
        quando = datetime.now(timezone.utc) + timedelta(minutes=max(d["espera_minutos"], 5))
        if d.get("regra"):
            campos["tentativas"] = t["tentativas"]  # erro conhecido e passageiro não gasta tentativa
        ctx.banco.atualizar_tarefa(t["id"], executar_apos=quando.isoformat(timespec="seconds"), **campos)
        resumo.adiadas.append(f"{rotulo}: tenta de novo às {quando:%H:%M} UTC — {d['diagnostico']}")
    elif acao == "desistir":
        ctx.banco.atualizar_tarefa(t["id"], status="falhou", **campos)
        _descartar_item(ctx, t, d["diagnostico"])
        resumo.descartadas.append(f"{rotulo}: {d['diagnostico']}")
    else:
        campos["diagnostico"] = f"{d['diagnostico']}\nO que fazer: {d['o_que_fazer']}"
        ctx.banco.atualizar_tarefa(t["id"], status="escalada", **campos)
        resumo.escaladas.append(f"{rotulo}: {d['diagnostico']} → {d['o_que_fazer']}")


def executar(ctx: Contexto, tipos: list[str] | None = None, limite: int | None = None, encadear: bool = True) -> Resumo:
    """Executa as tarefas prontas. Com encadear=True, chama o Planejador depois de cada sucesso,
    para que o mesmo vídeo siga baixar → transcrever → curar → editar → publicar na mesma rodada."""
    limite = limite or ctx.cfg.gerencia.max_tarefas_por_rodada
    resumo = Resumo()
    for _ in range(limite):
        prontas = ctx.banco.tarefas_prontas(tipos)
        if not prontas:
            break
        t = prontas[0]
        log.info("executor: começando %s", _rotulo(t))
        try:
            resultado = trabalhos.TRABALHOS[t["tipo"]](ctx, t["alvo"])
        except Exception as exc:  # noqa: BLE001 — qualquer erro vira decisão do Executor
            _tratar_erro(ctx, t, exc, resumo)
            continue
        ctx.banco.atualizar_tarefa(t["id"], status="feita", resultado=resultado)
        resumo.feitas.append(f"{_rotulo(t)}: {resultado}")
        if encadear:
            planejador.planejar(ctx)
    return resumo
