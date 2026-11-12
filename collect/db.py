"""Acces base : migrations versionnees et etat des sources.

SQL ecrit a la main (SQLAlchemy Core + text()) et portable MySQL / SQLite.
Compatible SQLAlchemy 1.4 (image Airflow) et 2.0.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def maintenant_sql() -> str:
    """Horodatage UTC au format DATETIME accepte par MySQL et SQLite."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def moteur(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True, pool_pre_ping=True)


# --------------------------------------------------------------------------- migrations

def _instructions(sql: str) -> list[str]:
    sans_commentaires = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in sans_commentaires.split(";") if s.strip()]


def migrer(engine: Engine, dossier: Path) -> list[str]:
    """Applique, dans l'ordre, les fichiers V*.sql pas encore passes. Renvoie les versions appliquees."""
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version VARCHAR(100) NOT NULL PRIMARY KEY,"
            " applique_le DATETIME NOT NULL)"
        ))
    with engine.connect() as c:
        deja = {r[0] for r in c.execute(text("SELECT version FROM schema_migrations"))}

    appliquees = []
    for fichier in sorted(dossier.glob("V*.sql"), key=lambda p: int(re.match(r"V(\d+)", p.name).group(1))):
        version = fichier.stem
        if version in deja:
            continue
        with engine.begin() as c:
            for instr in _instructions(fichier.read_text(encoding="utf-8")):
                c.execute(text(instr))
            c.execute(
                text("INSERT INTO schema_migrations (version, applique_le) VALUES (:v, :t)"),
                {"v": version, "t": maintenant_sql()},
            )
        log.info("migration appliquee : %s", version)
        appliquees.append(version)
    return appliquees


# --------------------------------------------------------------------------- sources

@dataclass
class Source:
    nom: str
    type: str
    url_flux: str
    actif: bool
    derniere_reussite: Optional[str]
    echecs_consecutifs: int
    etag: Optional[str]
    last_modified: Optional[str]
    derniere_erreur: Optional[str]


_COLONNES = "nom, type, url_flux, actif, derniere_reussite, echecs_consecutifs, etag, last_modified, derniere_erreur"


def _source(ligne) -> Source:
    d = dict(ligne._mapping)
    d["actif"] = bool(d["actif"])
    d["derniere_reussite"] = str(d["derniere_reussite"]) if d["derniere_reussite"] else None
    return Source(**d)


def synchroniser_sources(engine: Engine, fichier_csv: Path) -> dict:
    """Aligne la table `sources` sur sources.csv (la liste editoriale fait foi).

    - nouvelle ligne du CSV -> insertion, active ;
    - URL modifiee -> mise a jour et remise a zero de l'etat HTTP et du compteur ;
    - source retiree du CSV -> desactivee (pas supprimee : on garde son historique).
    L'etat de collecte (reussite, echecs, etag) n'est jamais ecrase sinon.
    """
    with open(fichier_csv, encoding="utf-8", newline="") as f:
        lignes = [l for l in csv.DictReader(f) if (l.get("nom") or "").strip()]

    bilan = {"ajoutees": 0, "modifiees": 0, "desactivees": 0}
    with engine.begin() as c:
        existantes = {s.nom: s for s in map(_source, c.execute(text(f"SELECT {_COLONNES} FROM sources")))}
        noms_csv = set()
        for l in lignes:
            nom, type_, url = l["nom"].strip(), l["type"].strip(), l["url_flux"].strip()
            noms_csv.add(nom)
            s = existantes.get(nom)
            if s is None:
                c.execute(
                    text("INSERT INTO sources (nom, type, url_flux, actif, echecs_consecutifs)"
                         " VALUES (:n, :t, :u, 1, 0)"),
                    {"n": nom, "t": type_, "u": url},
                )
                bilan["ajoutees"] += 1
            elif s.url_flux != url:
                c.execute(
                    text("UPDATE sources SET type = :t, url_flux = :u, actif = 1, echecs_consecutifs = 0,"
                         " etag = NULL, last_modified = NULL, derniere_erreur = NULL WHERE nom = :n"),
                    {"n": nom, "t": type_, "u": url},
                )
                bilan["modifiees"] += 1
            elif s.type != type_ or not s.actif:
                c.execute(text("UPDATE sources SET type = :t, actif = 1 WHERE nom = :n"), {"n": nom, "t": type_})
                bilan["modifiees"] += 1
        for nom, s in existantes.items():
            if nom not in noms_csv and s.actif:
                c.execute(text("UPDATE sources SET actif = 0 WHERE nom = :n"), {"n": nom})
                bilan["desactivees"] += 1
    return bilan


def lister_sources(engine: Engine) -> list[Source]:
    with engine.connect() as c:
        return [_source(r) for r in c.execute(text(f"SELECT {_COLONNES} FROM sources ORDER BY nom"))]


def sources_actives(engine: Engine) -> list[Source]:
    return [s for s in lister_sources(engine) if s.actif]


def noter_reussite(engine: Engine, nom: str, etag: Optional[str], last_modified: Optional[str]) -> None:
    with engine.begin() as c:
        c.execute(
            text("UPDATE sources SET derniere_reussite = :t, echecs_consecutifs = 0, derniere_erreur = NULL,"
                 " etag = :e, last_modified = :lm WHERE nom = :n"),
            {"t": maintenant_sql(), "e": etag, "lm": last_modified, "n": nom},
        )


def noter_echec(engine: Engine, nom: str, erreur: str) -> int:
    """Incremente le compteur d'echecs consecutifs et renvoie sa nouvelle valeur."""
    with engine.begin() as c:
        c.execute(
            text("UPDATE sources SET echecs_consecutifs = echecs_consecutifs + 1, derniere_erreur = :e"
                 " WHERE nom = :n"),
            {"e": erreur[:500], "n": nom},
        )
        return int(c.execute(text("SELECT echecs_consecutifs FROM sources WHERE nom = :n"), {"n": nom}).scalar_one())


def reveiller(engine: Engine, nom: str) -> bool:
    """Sortie manuelle du sommeil, une fois la cause corrigee (URL, blocage...)."""
    with engine.begin() as c:
        r = c.execute(
            text("UPDATE sources SET echecs_consecutifs = 0, derniere_erreur = NULL WHERE nom = :n"), {"n": nom}
        )
        return r.rowcount > 0
