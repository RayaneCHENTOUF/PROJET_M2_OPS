"""Un passage de traitement : brut -> normalisé -> règles -> articles_propres | rejets.

- Lit les fichiers bruts des derniers jours (partitions data/articles_bruts/{date}/).
- Saute tout article déjà traité (présent dans articles_propres OU rejets) :
  le traitement est idempotent, le relancer ne crée aucun doublon.
- Traite les nouveaux par ordre de publication : en cas de doublon, on garde le premier publié.
- Écrit tout le passage dans une seule transaction (tout ou rien).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from collect.config import RACINE, Config
from .normalisation import normaliser
from .regles import FENETRE_DOUBLONS, Contexte, appliquer

log = logging.getLogger("qualite")


@dataclass
class BilanTraitement:
    passage: str
    entres: int = 0
    gardes: int = 0
    rejetes: int = 0
    deja_traites: int = 0
    illisibles: int = 0
    par_regle: Counter = field(default_factory=Counter)
    par_source: dict[str, Counter] = field(default_factory=dict)  # source -> {entres, gardes, rejetes}

    def compter(self, source: str, cle: str) -> None:
        self.par_source.setdefault(source, Counter())[cle] += 1

    def ligne(self) -> str:
        regles = " ".join(f"{r}={n}" for r, n in sorted(self.par_regle.items()))
        return (f"passage={self.passage} entres={self.entres} gardes={self.gardes} rejetes={self.rejetes} "
                f"deja_traites={self.deja_traites} illisibles={self.illisibles}" + (f" {regles}" if regles else ""))


def vers_sql(iso: Optional[str]) -> Optional[str]:
    """ISO 8601 UTC -> « YYYY-MM-DD HH:MM:SS » (format DATETIME accepté par MySQL et SQLite)."""
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def chemin_relatif(chemin: Path) -> str:
    """Chemin du brut tel qu'on le stocke : relatif à la racine du dépôt si possible (portable hôte/Docker)."""
    try:
        return chemin.resolve().relative_to(RACINE).as_posix()
    except ValueError:
        return chemin.as_posix()


def chemin_absolu(chemin_brut: str) -> Path:
    """Inverse de chemin_relatif : d'un article propre, retrouver son fichier brut."""
    p = Path(chemin_brut)
    return p if p.is_absolute() else RACINE / p


def fichiers_bruts(dossier: Path, maintenant: datetime, jours: int) -> list[Path]:
    """Fichiers des `jours` dernières partitions (date de récupération), aujourd'hui compris."""
    dates = {(maintenant - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(jours)}
    return sorted(p for d in dates for p in (dossier / d).glob("*/*.json"))


def _ids_traites(engine: Engine) -> set[str]:
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT id FROM articles_propres UNION SELECT id FROM rejets"))}


def _contexte(engine: Engine, maintenant: datetime) -> Contexte:
    ctx = Contexte(maintenant=maintenant)
    depuis = (maintenant - FENETRE_DOUBLONS).strftime("%Y-%m-%d %H:%M:%S")
    with engine.connect() as c:
        lignes = c.execute(
            text("SELECT source, cle_titre FROM articles_propres WHERE recupere_le >= :d"
                 " ORDER BY publie_le, recupere_le"),
            {"d": depuis},
        )
        for source, cle in lignes:
            ctx.garder(source, cle)
    return ctx


def _ordre(a: dict) -> tuple:
    # publie_le d'abord : le premier publié est gardé, les reprises suivantes rejetées
    return (a["publie_le"] or a["recupere_le"] or "", a["source"], a["id"])


def traiter(cfg: Config, engine: Engine, jours: int = 2, maintenant: Optional[datetime] = None) -> BilanTraitement:
    maintenant = (maintenant or datetime.now(timezone.utc)).replace(microsecond=0)
    traite_le = maintenant.strftime("%Y-%m-%d %H:%M:%S")
    bilan = BilanTraitement(passage=maintenant.strftime("%Y%m%dT%H%M%SZ"))

    deja = _ids_traites(engine)
    nouveaux = []
    for chemin in fichiers_bruts(cfg.dossier_bruts, maintenant, jours):
        if chemin.stem in deja:
            bilan.deja_traites += 1
            continue
        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
            article = normaliser(brut)
        except (ValueError, KeyError) as e:  # fichier tronqué ou champ manquant : on le signale, on continue
            log.error('brut illisible chemin="%s" erreur="%s"', chemin, e)
            bilan.illisibles += 1
            continue
        article["chemin_brut"] = chemin_relatif(chemin)
        nouveaux.append(article)

    ctx = _contexte(engine, maintenant)
    propres, rejets = [], []
    for a in sorted(nouveaux, key=_ordre):
        bilan.entres += 1
        bilan.compter(a["source"], "entres")
        decision = appliquer(a, ctx)
        if decision is None:
            ctx.garder(a["source"], a["cle_titre"])
            propres.append({
                "id": a["id"], "source": a["source"], "lien": a["lien"][:1000],
                "titre": a["titre"][:500], "cle_titre": a["cle_titre"][:500], "extrait": a["extrait"],
                "publie_le": vers_sql(a["publie_le"]), "recupere_le": vers_sql(a["recupere_le"]),
                "rubrique": (a["rubrique"] or "")[:200] or None, "chemin_brut": a["chemin_brut"],
                "passage": bilan.passage, "traite_le": traite_le,
            })
            bilan.gardes += 1
            bilan.compter(a["source"], "gardes")
        else:
            regle, detail = decision
            rejets.append({
                "id": a["id"], "source": a["source"], "titre": a["titre"][:500], "regle": regle,
                "detail": detail[:500], "chemin_brut": a["chemin_brut"],
                "passage": bilan.passage, "traite_le": traite_le,
            })
            bilan.rejetes += 1
            bilan.par_regle[regle] += 1
            bilan.compter(a["source"], "rejetes")

    with engine.begin() as c:
        if propres:
            c.execute(text(
                "INSERT INTO articles_propres (id, source, lien, titre, cle_titre, extrait, publie_le,"
                " recupere_le, rubrique, chemin_brut, passage, traite_le) VALUES (:id, :source, :lien,"
                " :titre, :cle_titre, :extrait, :publie_le, :recupere_le, :rubrique, :chemin_brut,"
                " :passage, :traite_le)"), propres)
        if rejets:
            c.execute(text(
                "INSERT INTO rejets (id, source, titre, regle, detail, chemin_brut, passage, traite_le)"
                " VALUES (:id, :source, :titre, :regle, :detail, :chemin_brut, :passage, :traite_le)"), rejets)

    log.info(bilan.ligne())
    return bilan
