import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from cortes.agentes import editor, publicador
from cortes.agentes.cacador import Candidato, pontuar
from cortes.agentes.curador import ajustar_cortes
from cortes.config import Curador, Editor, Publicador
from cortes.db import Banco
from cortes.youtube import duracao_iso_para_seg


def _palavras(n, passo=0.5):
    return [{"i": k * passo, "f": k * passo + 0.4, "p": f"p{k}"} for k in range(n)]


def test_duracao_iso():
    assert duracao_iso_para_seg("PT1H2M3S") == 3723
    assert duracao_iso_para_seg("PT45S") == 45
    assert duracao_iso_para_seg("P1DT1S") == 86401
    assert duracao_iso_para_seg("lixo") == 0


def test_pontuar_prefere_video_que_cresce_mais_rapido():
    agora = datetime(2026, 10, 8, tzinfo=timezone.utc)
    base = dict(titulo="", descricao="", canal="", canal_id="", curtidas=0, comentarios=0, duracao_seg=600, licenca="youtube")
    rapido = Candidato(id="a", publicado_em=agora - timedelta(hours=10), visualizacoes=100_000, **base)
    lento = Candidato(id="b", publicado_em=agora - timedelta(days=10), visualizacoes=150_000, **base)
    assert pontuar(rapido, agora) > pontuar(lento, agora)


def test_ajustar_cortes_encaixa_em_palavras_e_respeita_limites():
    cfg = Curador(cortes_por_video=2, duracao_min_seg=5, duracao_max_seg=10, nota_minima=7)
    palavras = _palavras(100)  # 50s de fala
    propostas = [
        {"inicio": 1.1, "fim": 9.3, "nota": 9},     # ok
        {"inicio": 5.0, "fim": 12.0, "nota": 8},    # sobrepõe o primeiro -> descartado
        {"inicio": 20.0, "fim": 40.0, "nota": 8},   # longo demais -> encurtado para <= 10s
        {"inicio": 42.0, "fim": 48.0, "nota": 5},   # nota baixa
    ]
    saida = ajustar_cortes(propostas, palavras, 50.0, cfg)
    assert len(saida) == 2
    a, b = saida
    assert a["inicio"] == pytest.approx(0.85) and a["fim"] == pytest.approx(9.65)
    assert b["inicio"] == pytest.approx(19.85)
    assert b["fim"] - b["inicio"] <= 10
    assert not (a["inicio"] < b["fim"] and a["fim"] > b["inicio"])


def test_gerar_ass_destaca_palavra_falada():
    ass = editor.gerar_ass(_palavras(6), 0.0, 3.0, "Olha isso", Editor(palavras_por_legenda=3))
    dialogos = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert "OLHA ISSO" in dialogos[0]
    assert len(dialogos) == 1 + 6
    assert "{\\c&H0000FFFF}P0{\\c&H00FFFFFF} P1 P2" in dialogos[1]


def test_metadados_tem_shorts_e_credito():
    corte = {"titulo": "Ele não esperava", "descricao": "Trecho incrível", "hashtags": ["#podcast", "negócios", " "]}
    origem = {"id": "abc123", "titulo": "Episódio 10", "canal": "Canal X"}
    meta = publicador.montar_metadados(corte, origem, Publicador())
    assert meta["snippet"]["title"] == "Ele não esperava #Shorts"
    assert "youtube.com/watch?v=abc123" in meta["snippet"]["description"]
    assert meta["snippet"]["tags"] == ["podcast", "negócios"]
    assert meta["status"]["privacyStatus"] == "private"


def test_proximo_horario_espaca_publicacoes():
    agora = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    cfg = Publicador(intervalo_horas=3)
    assert publicador.proximo_horario(None, cfg, agora) == agora + timedelta(minutes=15)
    ultimo = (agora + timedelta(hours=2)).isoformat()
    assert publicador.proximo_horario(ultimo, cfg, agora) == agora + timedelta(hours=5)
    assert publicador.proximo_horario(None, Publicador(intervalo_horas=0), agora) is None


def test_banco_conta_publicacoes(tmp_path):
    b = Banco(tmp_path / "x.db")
    b.inserir_video(id="v1", titulo="t", status="curado")
    cid = b.inserir_corte("v1", inicio=0, fim=30, titulo="a", hashtags=["x"], nota=8)
    b.atualizar_corte(cid, status="publicado", publicado_em="2026-10-08T10:00:00+00:00")
    assert b.publicados_desde("2026-10-07T10:00:00+00:00") == 1
    assert b.publicados_desde("2026-10-09T10:00:00+00:00") == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg não instalado")
@pytest.mark.parametrize("estilo", ["desfocado", "recorte"])
def test_editor_gera_short_vertical(tmp_path, estilo):
    origem = tmp_path / "origem.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(origem)],
        check=True,
    )
    transcricao = tmp_path / "origem.json"
    transcricao.write_text(json.dumps({"segmentos": [], "palavras": _palavras(24)}))
    corte = {"inicio": 2.0, "fim": 8.0, "gancho": "Teste"}
    saida = editor.editar(origem, transcricao, corte, tmp_path / "shorts" / "c1.mp4", Editor(estilo=estilo))
    info = editor.verificar(saida)
    assert (info["largura"], info["altura"]) == (1080, 1920)
    assert info["duracao"] == pytest.approx(6.0, abs=0.2)
