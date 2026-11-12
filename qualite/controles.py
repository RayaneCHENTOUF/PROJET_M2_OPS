"""Contrôles de qualité : des FAITS vérifiés sur les vraies données, après chaque passage.

À ne pas confondre avec les tests (CI) : tests/test_regles.py vérifie le CODE des règles
sur une fixture ; ici on vérifie que la table articles_propres respecte bien ce qu'elle
promet, telle qu'elle est en base au moment du contrôle.

Trois faits :
    date_future   aucun article propre publié dans le futur (au-delà de la marge d'1 h)
    titre_vide    aucun article propre sans titre
    taux_rejet    aucune source ne voit plus de 80 % de ses articles rejetés (sur 24 h)

Chaque contrôle publie ses chiffres en métriques (table metriques_qualite + journal),
et si un fait est faux : ligne ALERTE dans le journal, webhook optionnel, code retour 1
(la tâche Airflow `controler` passe en rouge).

Choix : des assertions maison (SQL + Python) plutôt qu'un outil dédié
(Great Expectations, Soda). Justification dans docs/qualite.md § 6.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .regles import TOLERANCE_DATE_FUTURE

log = logging.getLogger("qualite.controles")

# =========================================================================== CONFIGURATION

SEUIL_TAUX_REJET = 0.8            # au-delà, une règle est trop gourmande ou le flux a changé
FENETRE_TAUX_REJET = timedelta(hours=24)
# En dessous de ce nombre d'articles traités sur la fenêtre, le taux n'est pas significatif
# (1 article sur 1 rejeté = 100 %) : la source est mesurée mais pas jugée.
MIN_ARTICLES_TAUX = 5
TOUTES_SOURCES = "*"


# =========================================================================== RÉSULTATS

@dataclass
class Resultat:
    controle: str
    ok: bool
    valeur: float          # ce qu'on a mesuré (nombre de cas, pire taux…)
    seuil: float           # ce qu'on tolère
    detail: str = ""


@dataclass
class Bilan:
    mesure_le: datetime
    resultats: list[Resultat] = field(default_factory=list)
    # (nom, source) -> valeur
    metriques: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.resultats)

    @property
    def echecs(self) -> list[Resultat]:
        return [r for r in self.resultats if not r.ok]

    def mesurer(self, nom: str, valeur: float, source: str = TOUTES_SOURCES) -> None:
        self.metriques[(nom, source)] = float(valeur)


def _sql(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# =========================================================================== LES TROIS FAITS

def controle_date_future(engine: Engine, bilan: Bilan) -> Resultat:
    limite = _sql(bilan.mesure_le + TOLERANCE_DATE_FUTURE)
    with engine.connect() as c:
        lignes = c.execute(text(
            "SELECT id, source, publie_le FROM articles_propres WHERE publie_le > :l ORDER BY publie_le DESC"
        ), {"l": limite}).fetchall()
    bilan.mesurer("dates_futures", len(lignes))
    exemples = ", ".join(f"{s} {p} ({i})" for i, s, p in lignes[:3])
    return Resultat("date_future", not lignes, len(lignes), 0,
                    f"{len(lignes)} article(s) publiés après {limite} UTC" + (f" : {exemples}" if exemples else ""))


def controle_titre_vide(engine: Engine, bilan: Bilan) -> Resultat:
    with engine.connect() as c:
        lignes = c.execute(text(
            "SELECT id, source FROM articles_propres WHERE TRIM(titre) = ''"
        )).fetchall()
    bilan.mesurer("titres_vides", len(lignes))
    exemples = ", ".join(f"{s} ({i})" for i, s in lignes[:3])
    return Resultat("titre_vide", not lignes, len(lignes), 0,
                    f"{len(lignes)} article(s) sans titre" + (f" : {exemples}" if exemples else ""))


def controle_taux_rejet(engine: Engine, bilan: Bilan, seuil: float = SEUIL_TAUX_REJET,
                        fenetre: timedelta = FENETRE_TAUX_REJET,
                        min_articles: int = MIN_ARTICLES_TAUX) -> Resultat:
    depuis = _sql(bilan.mesure_le - fenetre)
    with engine.connect() as c:
        gardes = dict(c.execute(text(
            "SELECT source, COUNT(*) FROM articles_propres WHERE traite_le >= :d GROUP BY source"), {"d": depuis}).fetchall())
        rejetes = dict(c.execute(text(
            "SELECT source, COUNT(*) FROM rejets WHERE traite_le >= :d GROUP BY source"), {"d": depuis}).fetchall())

    total_g, total_r = sum(gardes.values()), sum(rejetes.values())
    bilan.mesurer("articles_entres", total_g + total_r)
    bilan.mesurer("articles_gardes", total_g)
    bilan.mesurer("articles_rejetes", total_r)
    if total_g + total_r:
        bilan.mesurer("taux_rejet", total_r / (total_g + total_r))

    fautives, non_jugees, pire = [], [], 0.0
    for source in sorted(set(gardes) | set(rejetes)):
        g, r = gardes.get(source, 0), rejetes.get(source, 0)
        taux = r / (g + r)
        bilan.mesurer("articles_entres", g + r, source)
        bilan.mesurer("articles_gardes", g, source)
        bilan.mesurer("articles_rejetes", r, source)
        bilan.mesurer("taux_rejet", taux, source)
        if g + r < min_articles:
            non_jugees.append(source)
            continue
        pire = max(pire, taux)
        if taux > seuil:
            fautives.append(f"{source} {taux:.0%} ({r}/{g + r})")

    detail = (f"sources au-dessus de {seuil:.0%} sur {fenetre.total_seconds() / 3600:.0f} h : " + ", ".join(fautives)
              if fautives else f"pire taux {pire:.0%} (seuil {seuil:.0%}, fenêtre {fenetre.total_seconds() / 3600:.0f} h)")
    if non_jugees:
        detail += f" ; non jugées (< {min_articles} articles) : {', '.join(non_jugees)}"
    return Resultat("taux_rejet", not fautives, pire, seuil, detail)


# =========================================================================== PASSAGE

def publier(engine: Engine, bilan: Bilan) -> None:
    """Écrit les métriques (table metriques_qualite) et les journalise, une ligne par mesure."""
    mesure_le = _sql(bilan.mesure_le)
    for r in bilan.resultats:
        bilan.mesurer(f"controle_{r.controle}", 1 if r.ok else 0)
    lignes = [{"m": mesure_le, "n": nom, "s": source, "v": valeur}
              for (nom, source), valeur in sorted(bilan.metriques.items())]
    with engine.begin() as c:
        # deux contrôles dans la même seconde (relance manuelle) : la dernière mesure remplace l'autre
        c.execute(text("DELETE FROM metriques_qualite WHERE mesure_le = :m"), {"m": mesure_le})
        c.execute(text("INSERT INTO metriques_qualite (mesure_le, nom, source, valeur) VALUES (:m, :n, :s, :v)"),
                  lignes)
    for l in lignes:
        log.info('metrique nom=%s source="%s" valeur=%g', l["n"], l["s"], l["v"])


def alerter(bilan: Bilan, webhook: Optional[str] = None) -> None:
    """Une ligne ALERTE par fait faux ; et, si un webhook est configuré, un message (Slack/Discord/Teams)."""
    for r in bilan.echecs:
        log.error("ALERTE controle=%s valeur=%g seuil=%g : %s", r.controle, r.valeur, r.seuil, r.detail)
    webhook = webhook if webhook is not None else os.getenv("GAZETTE_ALERTE_WEBHOOK")
    if not webhook or bilan.ok:
        return
    message = "La Gazette : contrôle(s) de qualité en échec\n" + "\n".join(
        f"- {r.controle} : {r.detail}" for r in bilan.echecs)
    try:
        import requests

        # « text » pour Slack/Teams, « content » pour Discord
        requests.post(webhook, json={"text": message, "content": message}, timeout=10).raise_for_status()
        log.info("alerte envoyée au webhook")
    except Exception as e:  # l'alerte du journal et la tâche rouge restent : on ne masque rien
        log.error("envoi de l'alerte au webhook impossible : %s", e)


def controler(engine: Engine, maintenant: Optional[datetime] = None, seuil_rejet: float = SEUIL_TAUX_REJET,
              webhook: Optional[str] = None) -> Bilan:
    bilan = Bilan(mesure_le=(maintenant or datetime.now(timezone.utc)).replace(microsecond=0))
    bilan.resultats = [
        controle_date_future(engine, bilan),
        controle_titre_vide(engine, bilan),
        controle_taux_rejet(engine, bilan, seuil=seuil_rejet),
    ]
    for r in bilan.resultats:
        log.info("controle=%s ok=%s valeur=%g seuil=%g : %s", r.controle, r.ok, r.valeur, r.seuil, r.detail)
    publier(engine, bilan)
    alerter(bilan, webhook)
    return bilan
