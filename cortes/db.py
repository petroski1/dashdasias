"""Estado do pipeline em SQLite: evita processar/postar a mesma coisa duas vezes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    titulo TEXT,
    canal TEXT,
    canal_id TEXT,
    visualizacoes INTEGER,
    duracao_seg INTEGER,
    publicado_em TEXT,
    nota REAL,
    status TEXT NOT NULL DEFAULT 'encontrado',  -- encontrado|baixado|transcrito|curado|erro|descartado
    arquivo TEXT,
    transcricao TEXT,
    erro TEXT,
    criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cortes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    inicio REAL NOT NULL,
    fim REAL NOT NULL,
    titulo TEXT,
    descricao TEXT,
    hashtags TEXT,
    gancho TEXT,
    nota REAL,
    status TEXT NOT NULL DEFAULT 'curado',  -- curado|editado|publicado|erro|reprovado
    arquivo TEXT,
    capa TEXT,
    youtube_id TEXT,
    publicar_em TEXT,
    erro TEXT,
    criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
    publicado_em TEXT
);
CREATE TABLE IF NOT EXISTS tarefas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,                        -- cacar|baixar|transcrever|curar|editar|publicar
    alvo TEXT NOT NULL DEFAULT '',             -- id do vídeo ou do corte
    status TEXT NOT NULL DEFAULT 'pendente',   -- pendente|feita|falhou|escalada|cancelada
    tentativas INTEGER NOT NULL DEFAULT 0,
    executar_apos TEXT,
    resultado TEXT,
    erro TEXT,
    diagnostico TEXT,
    criado_em TEXT,
    atualizado_em TEXT
);
CREATE TABLE IF NOT EXISTS ajustes (chave TEXT PRIMARY KEY, valor TEXT);
"""

# tarefas nesses status ainda "seguram" o item: o Planejador não cria outra igual
ABERTAS = ("pendente", "escalada")
# ordem de execução: termina primeiro o que está mais perto de virar Short publicado
ORDEM_TIPOS = ("publicar", "editar", "curar", "transcrever", "baixar", "cacar")


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Banco:
    def __init__(self, caminho: str | Path):
        self.con = sqlite3.connect(str(caminho))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)
        # bancos criados antes do Agente Capista não têm a coluna "capa"
        colunas = {r["name"] for r in self.con.execute("PRAGMA table_info(cortes)")}
        if "capa" not in colunas:
            self.con.execute("ALTER TABLE cortes ADD COLUMN capa TEXT")
            self.con.commit()

    def video_existe(self, video_id: str) -> bool:
        return self.con.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone() is not None

    def inserir_video(self, **campos) -> None:
        cols = ", ".join(campos)
        marcas = ", ".join("?" for _ in campos)
        self.con.execute(f"INSERT OR IGNORE INTO videos ({cols}) VALUES ({marcas})", tuple(campos.values()))
        self.con.commit()

    def atualizar_video(self, video_id: str, **campos) -> None:
        self._atualizar("videos", "id", video_id, campos)

    def videos(self, status: str) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM videos WHERE status=? ORDER BY nota DESC", (status,)).fetchall()

    def video(self, video_id: str) -> sqlite3.Row:
        return self.con.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()

    def inserir_corte(self, video_id: str, **campos) -> int:
        if isinstance(campos.get("hashtags"), list):
            campos["hashtags"] = json.dumps(campos["hashtags"], ensure_ascii=False)
        campos["video_id"] = video_id
        cols = ", ".join(campos)
        marcas = ", ".join("?" for _ in campos)
        cur = self.con.execute(f"INSERT INTO cortes ({cols}) VALUES ({marcas})", tuple(campos.values()))
        self.con.commit()
        return cur.lastrowid

    def atualizar_corte(self, corte_id: int, **campos) -> None:
        self._atualizar("cortes", "id", corte_id, campos)

    def corte(self, corte_id: int) -> sqlite3.Row:
        return self.con.execute("SELECT * FROM cortes WHERE id=?", (corte_id,)).fetchone()

    def cortes(self, status: str) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM cortes WHERE status=? ORDER BY nota DESC", (status,)).fetchall()

    def publicados_desde(self, iso_inicio: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM cortes WHERE status='publicado' AND publicado_em >= ?", (iso_inicio,)
        ).fetchone()[0]

    def ultimo_agendamento(self) -> str | None:
        return self.con.execute("SELECT MAX(publicar_em) FROM cortes WHERE publicar_em IS NOT NULL").fetchone()[0]

    # --- tarefas ---------------------------------------------------------------------------

    def criar_tarefa(self, tipo: str, alvo: str = "") -> int | None:
        """Cria a tarefa se ainda não houver uma aberta igual. Devolve o id ou None."""
        if self.con.execute(
            f"SELECT 1 FROM tarefas WHERE tipo=? AND alvo=? AND status IN {ABERTAS}", (tipo, alvo)
        ).fetchone():
            return None
        agora = agora_iso()
        cur = self.con.execute(
            "INSERT INTO tarefas (tipo, alvo, executar_apos, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
            (tipo, alvo, agora, agora, agora),
        )
        self.con.commit()
        return cur.lastrowid

    def tarefa(self, tarefa_id: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()

    def tarefas_prontas(self, tipos: list[str] | None = None) -> list[sqlite3.Row]:
        linhas = self.con.execute(
            "SELECT * FROM tarefas WHERE status='pendente' AND executar_apos <= ? ORDER BY id", (agora_iso(),)
        ).fetchall()
        if tipos:
            linhas = [t for t in linhas if t["tipo"] in tipos]
        return sorted(linhas, key=lambda t: ORDEM_TIPOS.index(t["tipo"]) if t["tipo"] in ORDEM_TIPOS else 99)

    def tarefas_com_status(self, *status: str, limite: int = 50) -> list[sqlite3.Row]:
        marcas = ", ".join("?" for _ in status)
        return self.con.execute(
            f"SELECT * FROM tarefas WHERE status IN ({marcas}) ORDER BY atualizado_em DESC LIMIT ?", (*status, limite)
        ).fetchall()

    def tarefas_abertas(self, tipo: str) -> int:
        return self.con.execute(
            f"SELECT COUNT(*) FROM tarefas WHERE tipo=? AND status IN {ABERTAS}", (tipo,)
        ).fetchone()[0]

    def ultima_execucao(self, tipo: str) -> str | None:
        return self.con.execute(
            "SELECT MAX(atualizado_em) FROM tarefas WHERE tipo=? AND status IN ('feita', 'falhou')", (tipo,)
        ).fetchone()[0]

    def atualizar_tarefa(self, tarefa_id: int, **campos) -> None:
        campos["atualizado_em"] = agora_iso()
        self._atualizar("tarefas", "id", tarefa_id, campos)

    # --- ajustes (ex.: publicações pausadas) --------------------------------------------------

    def ajuste(self, chave: str) -> str | None:
        linha = self.con.execute("SELECT valor FROM ajustes WHERE chave=?", (chave,)).fetchone()
        return linha[0] if linha else None

    def definir_ajuste(self, chave: str, valor: str | None) -> None:
        self.con.execute("INSERT OR REPLACE INTO ajustes (chave, valor) VALUES (?, ?)", (chave, valor))
        self.con.commit()

    def contagem(self, tabela: str) -> dict[str, int]:
        return dict(self.con.execute(f"SELECT status, COUNT(*) FROM {tabela} GROUP BY status").fetchall())

    def _atualizar(self, tabela: str, chave: str, valor, campos: dict) -> None:
        sets = ", ".join(f"{c}=?" for c in campos)
        self.con.execute(f"UPDATE {tabela} SET {sets} WHERE {chave}=?", (*campos.values(), valor))
        self.con.commit()
