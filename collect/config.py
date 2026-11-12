"""Configuration du collecteur, lue depuis l'environnement (ou un fichier .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

USER_AGENT_DEFAUT = (
    "LaGazetteBot/0.1 (revue de presse pedagogique EFREI; "
    "+https://github.com/DZjeff05/OPS-LAGAZETTE)"
)


@dataclass(frozen=True)
class Config:
    database_url: str
    dossier_bruts: Path
    fichier_sources: Path
    dossier_migrations: Path
    user_agent: str = USER_AGENT_DEFAUT
    timeout_connexion: float = 5.0   # secondes pour ouvrir la connexion
    timeout_lecture: float = 20.0    # secondes max entre deux paquets recus
    tentatives: int = 3              # essais par source et par passage (erreurs transitoires)
    backoff_base: float = 2.0        # attente = base * 2^(essai-1) : 2 s, 4 s, ...
    backoff_max: float = 60.0
    seuil_sommeil: int = 3           # echecs consecutifs avant mise en sommeil
    taille_max_resume: int = 1000    # on garde un extrait, jamais l'article entier

    def avec(self, **changements) -> "Config":
        return replace(self, **changements)

    @classmethod
    def depuis_env(cls) -> "Config":
        try:
            from dotenv import load_dotenv

            load_dotenv(RACINE / ".env")
        except ImportError:  # python-dotenv est optionnel (ex. image Airflow)
            pass

        data = Path(os.getenv("GAZETTE_DATA_DIR", RACINE / "data"))
        return cls(
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{(data / 'gazette.db').as_posix()}"),
            dossier_bruts=data / "articles_bruts",
            fichier_sources=Path(os.getenv("GAZETTE_SOURCES_CSV", RACINE / "sources" / "sources.csv")),
            dossier_migrations=Path(os.getenv("GAZETTE_MIGRATIONS", RACINE / "sql" / "migrations")),
            user_agent=os.getenv("GAZETTE_USER_AGENT", USER_AGENT_DEFAUT),
            tentatives=int(os.getenv("GAZETTE_TENTATIVES", "3")),
            backoff_base=float(os.getenv("GAZETTE_BACKOFF_BASE", "2")),
            seuil_sommeil=int(os.getenv("GAZETTE_SEUIL_SOMMEIL", "3")),
        )
