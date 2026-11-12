"""Rapport de qualité du jour : rapports/qualite-YYYY-MM-DD.md, réécrit à chaque passage.

Un fichier par jour (UTC) : le bilan cumulé de la journée, le détail du dernier passage,
et de quoi repérer une règle trop gourmande (top 5, exemples de titres rejetés par règle).
Tout est relu dans la base (articles_propres, rejets, metriques_qualite) : le rapport
peut être régénéré pour n'importe quel jour (`python -m qualite rapport --date 2026-11-12`).

Le rapport ne contient que ce qu'on a le droit de republier : titres, sources, règles.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .regles import REGLES

log = logging.getLogger("qualite.rapport")

# =========================================================================== CONFIGURATION

SEUIL_SURVEILLANCE = 0.5   # une source qui perd plus de la moitié de ses articles mérite un œil
MIN_ARTICLES_SURVEILLANCE = 5
NB_TOP = 5
NB_EXEMPLES = 3
DOC = "docs/qualite.md"


# =========================================================================== DONNÉES DU JOUR

@dataclass
class Rejet:
    source: str
    titre: str
    regle: str
    detail: str
    passage: str
    traite_le: str


@dataclass
class Jour:
    jour: date
    gardes: Counter          # source -> nb gardés
    rejets: list[Rejet]      # plus récents d'abord
    passages: list[str]      # passages ayant traité des articles ce jour-là (ordre chronologique)
    gardes_par_passage: Counter
    controles: dict[str, bool]
    controles_le: Optional[str]

    @property
    def rejets_par_source(self) -> Counter:
        return Counter(r.source for r in self.rejets)

    @property
    def sources(self) -> list[str]:
        return sorted(set(self.gardes) | set(self.rejets_par_source))


def _bornes(jour: date) -> dict:
    debut = datetime(jour.year, jour.month, jour.day)
    return {"debut": f"{debut:%Y-%m-%d %H:%M:%S}", "fin": f"{debut + timedelta(days=1):%Y-%m-%d %H:%M:%S}"}


def charger(engine: Engine, jour: date) -> Jour:
    b = _bornes(jour)
    jour_sql = "traite_le >= :debut AND traite_le < :fin"
    with engine.connect() as c:
        gardes = Counter(dict(c.execute(text(
            f"SELECT source, COUNT(*) FROM articles_propres WHERE {jour_sql} GROUP BY source"), b).fetchall()))
        gardes_par_passage = Counter(dict(c.execute(text(
            f"SELECT passage, COUNT(*) FROM articles_propres WHERE {jour_sql} GROUP BY passage"), b).fetchall()))
        rejets = [Rejet(*map(str, r)) for r in c.execute(text(
            f"SELECT source, titre, regle, COALESCE(detail, ''), passage, traite_le FROM rejets WHERE {jour_sql}"
            " ORDER BY traite_le DESC, source, titre"), b)]
        # dernier contrôle du jour : controle_<nom> vaut 1 (fait vrai) ou 0 (fait faux)
        controles_le = c.execute(text(
            "SELECT MAX(mesure_le) FROM metriques_qualite WHERE mesure_le >= :debut AND mesure_le < :fin"
            " AND nom LIKE 'controle_%'"), b).scalar()
        controles = {}
        if controles_le:
            controles = {nom.removeprefix("controle_"): valeur == 1 for nom, valeur in c.execute(text(
                "SELECT nom, valeur FROM metriques_qualite WHERE mesure_le = :m AND nom LIKE 'controle_%'"
                " AND source = '*' ORDER BY nom"), {"m": controles_le})}
    passages = sorted(set(gardes_par_passage) | {r.passage for r in rejets})
    return Jour(jour, gardes, rejets, passages, gardes_par_passage, controles,
                str(controles_le)[:16] if controles_le else None)


# =========================================================================== MISE EN FORME

def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f} %".replace(".", ",") if total else "–"


def _cellule(texte: str, longueur: int = 90) -> str:
    texte = texte if len(texte) <= longueur else texte[: longueur - 1] + "…"
    return texte.replace("|", "\\|").replace("\n", " ")


def _passage_lisible(passage: str) -> str:
    try:
        return datetime.strptime(passage, "%Y%m%dT%H%M%SZ").strftime("%H:%M UTC")
    except ValueError:
        return passage


def construire(j: Jour, maintenant: datetime) -> str:
    descriptions = {r.id: r.description for r in REGLES}
    rej_source = j.rejets_par_source
    total_g, total_r = sum(j.gardes.values()), len(j.rejets)
    total = total_g + total_r
    lignes = [
        f"# Rapport qualité — {j.jour:%Y-%m-%d}",
        "",
        f"Généré le {maintenant:%Y-%m-%d à %H:%M} UTC · {len(j.passages)} passage(s) ayant traité des articles"
        f" · journée en UTC · règles et contrôles décrits dans `{DOC}`.",
        "",
    ]

    # ------------------------------------------------------------- en bref
    dernier = j.passages[-1] if j.passages else None
    if dernier:
        g_d = j.gardes_par_passage.get(dernier, 0)
        r_d = sum(1 for r in j.rejets if r.passage == dernier)
        lignes += [
            "## En bref", "",
            f"| | Journée | Dernier passage ({_passage_lisible(dernier)}) |",
            "|---|---:|---:|",
            f"| **Entrés** | {total} | {g_d + r_d} |",
            f"| **Gardés** (`articles_propres`) | {total_g} | {g_d} |",
            f"| **Rejetés** (`rejets`) | {total_r} ({_pct(total_r, total)}) | {r_d} ({_pct(r_d, g_d + r_d)}) |",
            "",
        ]
    else:
        lignes += ["## En bref", "", "Aucun article traité ce jour-là.", ""]

    # ------------------------------------------------------------- contrôles
    lignes += ["## Contrôles de qualité", ""]
    if j.controles:
        lignes += [f"Dernier contrôle : {j.controles_le} UTC.", "", "| Contrôle | Résultat |", "|---|---|"]
        lignes += [f"| `{nom}` | {'✅ vrai' if ok else '❌ **FAUX** (alerte)'} |" for nom, ok in j.controles.items()]
    else:
        lignes += ["Aucun contrôle enregistré ce jour-là."]
    lignes.append("")

    # ------------------------------------------------------------- à surveiller
    alertes = [f"Contrôle `{nom}` en échec." for nom, ok in j.controles.items() if not ok]
    for source in j.sources:
        g, r = j.gardes.get(source, 0), rej_source.get(source, 0)
        if g + r >= MIN_ARTICLES_SURVEILLANCE and r / (g + r) > SEUIL_SURVEILLANCE:
            principale, n = Counter(x.regle for x in j.rejets if x.source == source).most_common(1)[0]
            alertes.append(f"**{source}** perd {_pct(r, g + r)} de ses articles ({r}/{g + r}),"
                           f" surtout par `{principale}` ({n}). Règle trop gourmande ou flux qui a changé ?")
    regles_vides = [r.id for r in REGLES if not any(x.regle == r.id for x in j.rejets)]
    lignes += ["## À surveiller", ""]
    lignes += [f"- {a}" for a in alertes] or ["Rien à signaler : aucun contrôle en échec,"
                                               f" aucune source au-dessus de {SEUIL_SURVEILLANCE * 100:.0f} % de rejets."]
    lignes += ["", f"Règles qui n'ont rien rejeté aujourd'hui : {', '.join(f'`{r}`' for r in regles_vides) or 'aucune'}.", ""]

    # ------------------------------------------------------------- top 5
    paires = Counter((r.regle, r.source) for r in j.rejets)
    lignes += [f"## Top {NB_TOP} des rejets (règle × source)", ""]
    if paires:
        lignes += ["| # | Règle | Source | Rejets | Part des articles de la source | Exemple |", "|---:|---|---|---:|---:|---|"]
        for i, ((regle, source), n) in enumerate(paires.most_common(NB_TOP), 1):
            entres = j.gardes.get(source, 0) + rej_source[source]
            ex = next(r for r in j.rejets if r.regle == regle and r.source == source)
            lignes.append(f"| {i} | `{regle}` | {source} | {n} | {_pct(n, entres)} | « {_cellule(ex.titre, 70)} » |")
    else:
        lignes.append("Aucun rejet.")
    lignes.append("")

    # ------------------------------------------------------------- par règle
    par_regle = Counter(r.regle for r in j.rejets)
    lignes += ["## Rejets par règle", "", "| Règle | Ce qu'elle écarte | Rejets | Part des rejets | Sources touchées |",
               "|---|---|---:|---:|---|"]
    for r in REGLES:
        n = par_regle.get(r.id, 0)
        sources = Counter(x.source for x in j.rejets if x.regle == r.id)
        touchees = ", ".join(f"{s} ({k})" for s, k in sources.most_common())
        lignes.append(f"| `{r.id}` | {r.description} | {n} | {_pct(n, total_r)} | {touchees or '–'} |")
    for regle in sorted(set(par_regle) - set(descriptions)):  # règle disparue du code mais présente en base
        lignes.append(f"| `{regle}` | *(règle retirée du code)* | {par_regle[regle]} | {_pct(par_regle[regle], total_r)} | |")
    lignes.append("")

    # ------------------------------------------------------------- par source
    lignes += ["## Rejets par source", "", "| Source | Entrés | Gardés | Rejetés | Taux de rejet | Règles |",
               "|---|---:|---:|---:|---:|---|"]
    for source in sorted(j.sources, key=lambda s: (-rej_source.get(s, 0), s)):
        g, r = j.gardes.get(source, 0), rej_source.get(source, 0)
        regles = Counter(x.regle for x in j.rejets if x.source == source)
        detail = ", ".join(f"`{k}` {n}" for k, n in regles.most_common())
        lignes.append(f"| {source} | {g + r} | {g} | {r} | {_pct(r, g + r)} | {detail or '–'} |")
    lignes.append("")

    # ------------------------------------------------------------- exemples
    lignes += [f"## Exemples de titres rejetés ({NB_EXEMPLES} plus récents par règle)", "",
               "C'est ici qu'on repère un faux positif : un vrai article écarté à tort.", ""]
    if not j.rejets:
        lignes += ["Aucun rejet.", ""]
    for regle, n in par_regle.most_common():
        lignes += [f"### `{regle}` — {descriptions.get(regle, 'règle retirée du code')} ({n})", ""]
        for r in [x for x in j.rejets if x.regle == regle][:NB_EXEMPLES]:
            lignes.append(f"- {r.source} · « {_cellule(r.titre, 120)} » — {_cellule(r.detail, 120)}")
        lignes.append("")

    return "\n".join(lignes)


# =========================================================================== ÉCRITURE

def chemin_rapport(dossier: Path, jour: date) -> Path:
    return dossier / f"qualite-{jour:%Y-%m-%d}.md"


def generer(engine: Engine, dossier: Path, maintenant: Optional[datetime] = None,
            jour: Optional[date] = None) -> Path:
    """Écrit (ou réécrit) le rapport du jour. Écriture atomique : jamais de rapport à moitié écrit."""
    maintenant = (maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc)
    jour = jour or maintenant.date()
    contenu = construire(charger(engine, jour), maintenant)
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = chemin_rapport(dossier, jour)
    tmp = chemin.with_suffix(".md.tmp")
    tmp.write_text(contenu + "\n", encoding="utf-8")
    tmp.replace(chemin)
    log.info('rapport ecrit chemin="%s"', chemin)
    return chemin
