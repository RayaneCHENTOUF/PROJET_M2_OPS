"""Rétention : ce qu'on garde, combien de temps, et où vont les vieilles données.

Politique (docs/donnees.md, § Rétention) :

    articles bruts     30 j dans data/articles_bruts/, puis archivés : un .tar.gz par jour
                       dans data/archives/articles_bruts/ ; archives supprimées après 1 an
    rejets             30 j (le temps de relire les rapports et d'ajuster les règles)
    articles_propres   1 an (date de récupération)
    metriques_qualite  gardées (quelques Ko par jour : l'historique sert à voir les dérives)
    rapports/          gardés (un fichier Markdown par jour)

Pourquoi archiver les bruts au lieu de les supprimer à 30 j : un article propre est gardé
1 an, et il doit rester traçable jusqu'à son brut (`chemin_brut`). `retrouver_brut` lit
le brut là où il est : dans data/articles_bruts/ ou dans l'archive du jour.

Tourne une fois par jour (DAG `retention`). `--simulation` affiche sans rien toucher.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from collect.config import Config
from .traitement import chemin_absolu

log = logging.getLogger("qualite.retention")

# =========================================================================== CONFIGURATION

BRUTS_ACTIFS = timedelta(days=30)
ARCHIVES_BRUTS = timedelta(days=365)
REJETS = timedelta(days=30)
ARTICLES_PROPRES = timedelta(days=365)


@dataclass
class BilanRetention:
    simulation: bool
    partitions_archivees: list[str] = field(default_factory=list)
    archives_supprimees: list[str] = field(default_factory=list)
    rejets_supprimes: int = 0
    propres_supprimes: int = 0

    def ligne(self) -> str:
        return (f"simulation={self.simulation} partitions_archivees={len(self.partitions_archivees)}"
                f" archives_supprimees={len(self.archives_supprimees)} rejets_supprimes={self.rejets_supprimes}"
                f" propres_supprimes={self.propres_supprimes}")


def dossier_archives(cfg: Config) -> Path:
    return cfg.dossier_bruts.parent / "archives" / "articles_bruts"


def _jour(nom: str) -> Optional[date]:
    try:
        return date.fromisoformat(nom.removesuffix(".tar.gz"))
    except ValueError:
        return None


# =========================================================================== BRUTS

def archiver_partition(partition: Path, archives: Path) -> Path:
    """data/articles_bruts/2026-11-12/ -> data/archives/articles_bruts/2026-11-12.tar.gz, puis suppression.

    Les membres gardent leur chemin relatif (« 2026-11-12/le-monde/<id>.json »).
    Si l'archive existe déjà (passage précédent interrompu), son contenu est repris :
    rien n'est perdu. Écriture atomique, partition supprimée seulement après.
    """
    archives.mkdir(parents=True, exist_ok=True)
    cible = archives / f"{partition.name}.tar.gz"
    tmp = cible.with_name(cible.name + ".tmp")
    nouveaux = {f"{partition.name}/{p.relative_to(partition).as_posix()}": p
                for p in sorted(partition.rglob("*.json"))}
    with tarfile.open(tmp, "w:gz") as tar:
        if cible.exists():
            with tarfile.open(cible, "r:gz") as ancien:
                for membre in ancien.getmembers():
                    if membre.isfile() and membre.name not in nouveaux:
                        tar.addfile(membre, ancien.extractfile(membre))
        for nom, chemin in nouveaux.items():
            tar.add(chemin, arcname=nom)
    tmp.replace(cible)
    shutil.rmtree(partition)
    return cible


def retrouver_brut(chemin_brut: str, cfg: Config) -> dict:
    """D'un article propre (ou d'un rejet), son brut d'origine : fichier actif ou archive du jour."""
    chemin = chemin_absolu(chemin_brut)
    if chemin.exists():
        return json.loads(chemin.read_text(encoding="utf-8"))
    # « data/articles_bruts/2026-11-12/le-monde/<id>.json » -> membre « 2026-11-12/le-monde/<id>.json »
    partition, source, fichier = Path(chemin_brut).parts[-3:]
    archive = dossier_archives(cfg) / f"{partition}.tar.gz"
    if not archive.exists():
        raise FileNotFoundError(f"brut introuvable (ni actif, ni archivé) : {chemin_brut}")
    with tarfile.open(archive, "r:gz") as tar:
        try:
            contenu = tar.extractfile(f"{partition}/{source}/{fichier}")
        except KeyError:
            raise FileNotFoundError(f"brut absent de l'archive {archive.name} : {chemin_brut}") from None
        return json.load(io.TextIOWrapper(contenu, encoding="utf-8"))


# =========================================================================== PASSAGE

def appliquer(cfg: Config, engine: Engine, maintenant: Optional[datetime] = None,
              simulation: bool = False) -> BilanRetention:
    maintenant = maintenant or datetime.now(timezone.utc)
    bilan = BilanRetention(simulation=simulation)
    archives = dossier_archives(cfg)

    # 1. bruts de plus de 30 j -> archive du jour
    limite_bruts = (maintenant - BRUTS_ACTIFS).date()
    if cfg.dossier_bruts.exists():
        for partition in sorted(p for p in cfg.dossier_bruts.iterdir() if p.is_dir()):
            jour = _jour(partition.name)
            if jour and jour < limite_bruts:
                bilan.partitions_archivees.append(partition.name)
                if not simulation:
                    archiver_partition(partition, archives)

    # 2. archives de plus d'1 an -> supprimées
    limite_archives = (maintenant - ARCHIVES_BRUTS).date()
    if archives.exists():
        for archive in sorted(archives.glob("*.tar.gz")):
            jour = _jour(archive.name)
            if jour and jour < limite_archives:
                bilan.archives_supprimees.append(archive.name)
                if not simulation:
                    archive.unlink()

    # 3. tables : rejets 30 j, articles propres 1 an
    def _sql(d: datetime) -> str:
        return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    requetes = {
        "rejets_supprimes": ("rejets", "traite_le", _sql(maintenant - REJETS)),
        "propres_supprimes": ("articles_propres", "recupere_le", _sql(maintenant - ARTICLES_PROPRES)),
    }
    with engine.begin() as c:
        for attribut, (table, colonne, limite) in requetes.items():
            if simulation:
                n = c.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {colonne} < :l"), {"l": limite}).scalar()
            else:
                n = c.execute(text(f"DELETE FROM {table} WHERE {colonne} < :l"), {"l": limite}).rowcount
            setattr(bilan, attribut, n)

    for nom in bilan.partitions_archivees:
        log.info("partition %s : %s", nom, "à archiver" if simulation else "archivée")
    for nom in bilan.archives_supprimees:
        log.info("archive %s : %s", nom, "à supprimer" if simulation else "supprimée")
    log.info(bilan.ligne())
    return bilan
