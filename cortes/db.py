"""Estado do pipeline em SQLite: evita processar/postar a mesma coisa duas vezes."""
from __future__ import annotations

import json
import sqlite3
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
"""


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

    def cortes(self, status: str) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM cortes WHERE status=? ORDER BY nota DESC", (status,)).fetchall()

    def publicados_desde(self, iso_inicio: str) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM cortes WHERE status='publicado' AND publicado_em >= ?", (iso_inicio,)
        ).fetchone()[0]

    def ultimo_agendamento(self) -> str | None:
        return self.con.execute("SELECT MAX(publicar_em) FROM cortes WHERE publicar_em IS NOT NULL").fetchone()[0]

    def _atualizar(self, tabela: str, chave: str, valor, campos: dict) -> None:
        sets = ", ".join(f"{c}=?" for c in campos)
        self.con.execute(f"UPDATE {tabela} SET {sets} WHERE {chave}=?", (*campos.values(), valor))
        self.con.commit()
