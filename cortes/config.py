from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Fontes(BaseModel):
    canais_autorizados: list[str] = Field(default_factory=list)
    buscas_creative_commons: list[str] = Field(default_factory=list)


class Cacador(BaseModel):
    dias_maximos: int = 14
    visualizacoes_minimas: int = 50_000
    duracao_minima_seg: int = 240
    duracao_maxima_seg: int = 10_800
    videos_por_rodada: int = 2


class Curador(BaseModel):
    cortes_por_video: int = 3
    duracao_min_seg: float = 20
    duracao_max_seg: float = 58
    nota_minima: float = 7
    idioma: str = "pt-BR"


class Transcricao(BaseModel):
    modelo_whisper: str = "small"
    dispositivo: str = "cpu"


class Editor(BaseModel):
    estilo: Literal["desfocado", "recorte"] = "desfocado"
    legendas: bool = True
    palavras_por_legenda: int = 3
    fonte: str = "Arial"
    cor_destaque: str = "&H0000FFFF"


class Capa(BaseModel):
    ativo: bool = True
    frames_candidatos: int = 8
    fonte_arquivo: str = ""   # vazio = procura uma fonte negrito do sistema
    cor_destaque: str = "#FFD400"


class Publicador(BaseModel):
    privacidade: Literal["private", "unlisted", "public"] = "private"
    max_postagens_por_dia: int = 5
    intervalo_horas: float = 3
    categoria_id: str = "22"
    creditar_fonte: bool = True


class Gerencia(BaseModel):
    usar_gerente: bool = True          # False = roda só Planejador + Executor, sem o Gerente (Claude)
    max_tentativas: int = 3            # depois disso o Executor escala para um humano
    max_tarefas_por_rodada: int = 40
    intervalo_caca_horas: float = 2    # tempo mínimo entre duas buscas de vídeos novos


class Orquestrador(BaseModel):
    intervalo_minutos: int = 120
    pasta_dados: str = "dados"


class Config(BaseModel):
    fontes: Fontes = Field(default_factory=Fontes)
    cacador: Cacador = Field(default_factory=Cacador)
    curador: Curador = Field(default_factory=Curador)
    transcricao: Transcricao = Field(default_factory=Transcricao)
    editor: Editor = Field(default_factory=Editor)
    capa: Capa = Field(default_factory=Capa)
    publicador: Publicador = Field(default_factory=Publicador)
    gerencia: Gerencia = Field(default_factory=Gerencia)
    orquestrador: Orquestrador = Field(default_factory=Orquestrador)

    @property
    def pasta_dados(self) -> Path:
        p = Path(self.orquestrador.pasta_dados)
        p.mkdir(parents=True, exist_ok=True)
        return p


def carregar(caminho: str | Path = "config.yaml") -> Config:
    caminho = Path(caminho)
    if not caminho.exists():
        raise SystemExit(f"Arquivo {caminho} não encontrado. Copie config.example.yaml para {caminho}.")
    return Config.model_validate(yaml.safe_load(caminho.read_text(encoding="utf-8")) or {})
