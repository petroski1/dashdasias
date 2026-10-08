"""Testes da equipe administrativa: Planejador, Executor e ferramentas do Gerente (sem chamar APIs)."""
from datetime import datetime, timedelta, timezone

import pytest

from cortes import llm, trabalhos
from cortes.agentes import executor, gerente, planejador
from cortes.config import Config
from cortes.db import Banco
from cortes.trabalhos import Contexto, TentarDepois


@pytest.fixture
def ctx(tmp_path):
    cfg = Config.model_validate({"orquestrador": {"pasta_dados": str(tmp_path / "dados")},
                                 "cacador": {"videos_por_rodada": 1}})
    return Contexto(cfg=cfg, banco=Banco(tmp_path / "t.db"))


@pytest.fixture
def sem_ia(monkeypatch):
    """Falha o teste se alguém chamar o Claude sem querer."""
    def proibido(*a, **k):
        raise AssertionError("não deveria chamar o Claude")
    monkeypatch.setattr(llm, "perguntar_json", proibido)


def _fakes(monkeypatch, ctx, **sobrescritos):
    b = ctx.banco

    def avancar(novo):
        def f(ctx, alvo):
            b.atualizar_video(alvo, status=novo)
            return novo
        return f

    def curar(ctx, alvo):
        b.inserir_corte(alvo, inicio=0, fim=30, titulo="t", hashtags=[], nota=9)
        b.atualizar_video(alvo, status="curado")
        return "1 corte"

    def editar(ctx, alvo):
        b.atualizar_corte(int(alvo), status="editado")
        return "editado"

    def publicar(ctx, alvo):
        b.atualizar_corte(int(alvo), status="publicado", youtube_id="yt" + alvo,
                          publicado_em=datetime.now(timezone.utc).isoformat())
        return "publicado"

    def cacar(ctx, alvo):
        return "nada novo"

    fakes = {"cacar": cacar, "baixar": avancar("baixado"), "transcrever": avancar("transcrito"),
             "curar": curar, "editar": editar, "publicar": publicar}
    fakes.update(sobrescritos)
    monkeypatch.setattr(trabalhos, "TRABALHOS", fakes)


def test_planejador_cria_tarefas_sem_repetir(ctx):
    b = ctx.banco
    b.inserir_video(id="v1", titulo="a", status="encontrado")
    b.inserir_video(id="v2", titulo="b", status="baixado")
    criadas = planejador.planejar(ctx)
    assert ("baixar", "v1") in criadas and ("transcrever", "v2") in criadas
    assert ("cacar", "") not in criadas  # fila já tem 2 vídeos
    assert planejador.planejar(ctx) == []  # nada duplicado


def test_planejador_nao_caca_de_novo_logo_depois(ctx):
    assert planejador.planejar(ctx) == [("cacar", "")]
    t = ctx.banco.tarefas_prontas()[0]
    ctx.banco.atualizar_tarefa(t["id"], status="feita")
    assert planejador.planejar(ctx) == []


def test_planejador_respeita_limite_e_pausa(ctx):
    b = ctx.banco
    ctx.cfg.publicador.max_postagens_por_dia = 2
    b.inserir_video(id="v1", titulo="a", status="curado")
    for _ in range(3):
        b.atualizar_corte(b.inserir_corte("v1", inicio=0, fim=30, titulo="t", hashtags=[], nota=8), status="editado")
    b.atualizar_tarefa(b.criar_tarefa("cacar"), status="feita")
    assert [t for t, _ in planejador.planejar(ctx)] == ["publicar", "publicar"]

    b2 = Banco(":memory:")
    ctx2 = Contexto(cfg=ctx.cfg, banco=b2)
    b2.inserir_video(id="v1", titulo="a", status="curado")
    b2.atualizar_corte(b2.inserir_corte("v1", inicio=0, fim=30, titulo="t", hashtags=[], nota=8), status="editado")
    b2.definir_ajuste("publicacao_pausada", "1")
    assert "publicar" not in [t for t, _ in planejador.planejar(ctx2)]


def test_executor_leva_video_do_inicio_ao_fim_numa_rodada(ctx, monkeypatch, sem_ia):
    _fakes(monkeypatch, ctx)
    ctx.banco.inserir_video(id="v1", titulo="a", status="encontrado")
    planejador.planejar(ctx)
    resumo = executor.executar(ctx)
    assert ctx.banco.video("v1")["status"] == "curado"
    assert ctx.banco.contagem("cortes") == {"publicado": 1}
    assert len(resumo.feitas) == 6  # cacar, baixar, transcrever, curar, editar, publicar
    assert not resumo.escaladas


def test_executor_desiste_de_video_indisponivel_sem_usar_ia(ctx, monkeypatch, sem_ia):
    def baixar(ctx, alvo):
        raise RuntimeError("ERROR: [youtube] v1: Video unavailable")
    _fakes(monkeypatch, ctx, baixar=baixar)
    ctx.banco.inserir_video(id="v1", titulo="a", status="encontrado")
    ctx.banco.criar_tarefa("baixar", "v1")
    resumo = executor.executar(ctx, encadear=False)
    assert ctx.banco.video("v1")["status"] == "erro"
    assert ctx.banco.tarefas_com_status("falhou")[0]["tipo"] == "baixar"
    assert len(resumo.descartadas) == 1


def test_executor_adia_cota_sem_gastar_tentativa(ctx, monkeypatch, sem_ia):
    def publicar(ctx, alvo):
        raise RuntimeError('<HttpError 403 "quotaExceeded">')
    _fakes(monkeypatch, ctx, publicar=publicar)
    ctx.banco.inserir_video(id="v1", titulo="a", status="curado")
    cid = ctx.banco.inserir_corte("v1", inicio=0, fim=30, titulo="t", hashtags=[], nota=8)
    tid = ctx.banco.criar_tarefa("publicar", str(cid))
    executor.executar(ctx, encadear=False)
    t = ctx.banco.tarefa(tid)
    assert t["status"] == "pendente" and t["tentativas"] == 0
    assert datetime.fromisoformat(t["executar_apos"]) > datetime.now(timezone.utc) + timedelta(hours=5)


def test_executor_tentar_depois_nao_e_erro(ctx, monkeypatch, sem_ia):
    def publicar(ctx, alvo):
        raise TentarDepois(60, "limite diário")
    _fakes(monkeypatch, ctx, publicar=publicar)
    tid = ctx.banco.criar_tarefa("publicar", "1")
    resumo = executor.executar(ctx, encadear=False)
    assert ctx.banco.tarefa(tid)["tentativas"] == 0
    assert resumo.adiadas and not resumo.escaladas


def test_executor_usa_claude_e_escala_depois_do_limite(ctx, monkeypatch):
    chamadas = []

    def falso(sistema, usuario, esquema, esforco="medium"):
        chamadas.append(usuario)
        return {"acao": "tentar_novamente", "espera_minutos": 0, "diagnostico": "falha estranha", "o_que_fazer": ""}

    def transcrever(ctx, alvo):
        raise RuntimeError("erro misterioso no whisper")

    monkeypatch.setattr(llm, "perguntar_json", falso)
    _fakes(monkeypatch, ctx, transcrever=transcrever)
    ctx.banco.inserir_video(id="v1", titulo="Vídeo X", canal="C", status="baixado")
    tid = ctx.banco.criar_tarefa("transcrever", "v1")
    resumo = executor.executar(ctx, encadear=False)

    t = ctx.banco.tarefa(tid)
    assert len(chamadas) == ctx.cfg.gerencia.max_tentativas
    assert "Vídeo X" in chamadas[0] and "erro misterioso" in chamadas[0]
    assert t["status"] == "escalada" and t["tentativas"] == 3
    assert ctx.banco.video("v1")["status"] == "baixado"  # item fica esperando o humano
    assert len(resumo.escaladas) == 1


def test_ferramentas_do_gerente(ctx, monkeypatch, sem_ia):
    _fakes(monkeypatch, ctx)
    ctx.banco.inserir_video(id="v1", titulo="a", status="encontrado")
    f = {t.name: t for t in gerente._ferramentas(ctx)}

    assert "Vídeos por status" in f["ver_painel"].call({})
    assert "baixar v1" in f["planejar_tarefas"].call({})
    assert "Feitas" in f["executar_tarefas"].call({"limite": 50})
    assert ctx.banco.contagem("cortes") == {"publicado": 1}

    f["pausar_publicacoes"].call({"motivo": "teste"})
    assert "PAUSADAS" in f["ver_painel"].call({})
    f["retomar_publicacoes"].call({})

    ctx.banco.inserir_video(id="v2", titulo="b", status="encontrado")
    tid = ctx.banco.criar_tarefa("baixar", "v2")
    ctx.banco.atualizar_tarefa(tid, status="escalada", diagnostico="disco cheio")
    assert "disco cheio" in f["listar_problemas"].call({})
    assert "reaberta" in f["reabrir_tarefa"].call({"tarefa_id": tid, "motivo": "liberei espaço"})
    assert "cancelada" in f["cancelar_tarefa"].call({"tarefa_id": tid, "motivo": "não vale a pena"})
    assert ctx.banco.video("v2")["status"] == "erro"


def test_executor_escala_youtube_sem_login_sem_derrubar_a_rodada(ctx, monkeypatch, sem_ia):
    from cortes.youtube import YouTubeSemLogin

    def cacar(ctx, alvo):
        raise YouTubeSemLogin("Sem token do YouTube. Rode: python -m cortes auth")

    def baixar(ctx, alvo):
        raise RuntimeError("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser")

    _fakes(monkeypatch, ctx, cacar=cacar, baixar=baixar)
    ctx.banco.inserir_video(id="v1", titulo="a", status="encontrado")
    ctx.banco.criar_tarefa("cacar")
    ctx.banco.criar_tarefa("baixar", "v1")
    resumo = executor.executar(ctx, encadear=False)
    assert len(resumo.escaladas) == 2
    textos = " ".join(resumo.escaladas)
    assert "python -m cortes auth" in textos and "cookies_navegador" in textos
