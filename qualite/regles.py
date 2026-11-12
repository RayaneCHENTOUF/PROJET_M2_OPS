"""Règles d'exclusion : chacune a un identifiant (R01_…), documenté dans docs/qualite.md.

Une règle reçoit un article NORMALISÉ (voir normalisation.normaliser) et le contexte
du passage ; elle renvoie None si elle laisse passer l'article, sinon un court
« détail » qui explique le rejet (le motif trouvé, le doublon visé…).

Les règles sont appliquées dans l'ordre de REGLES : la première qui rejette
l'emporte, c'est son identifiant qui est enregistré dans la table `rejets`.
L'ordre va du plus certain (titre vide) au plus discutable (doublon approximatif).

Pour ajuster une règle, on modifie les motifs et seuils de la section CONFIGURATION,
puis on note le changement et sa raison dans docs/qualite.md (journal des ajustements).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .normalisation import _MOJIBAKE, sans_accents

# =========================================================================== CONFIGURATION
# Les motifs s'appliquent sur un texte en minuscules SANS accents (« télé » -> « tele »).
# Ils sont volontairement précis : un mot isolé (« concours », « prix ») attrape de vrais
# articles (voir docs/qualite.md, § Faux positifs).

TOLERANCE_DATE_FUTURE = timedelta(hours=1)  # horloges et fuseaux mal déclarés : 1 h de marge

MOTIFS_PUBLI = [
    r"\bcontenus? (sponsorises?|partenaires?|de marque)\b",
    r"\bpubli[ -]?(reportage|redactionnel|information|communique)s?\b",
    r"\ben partenariat avec\b",
    r"\bsponsorise\b",
    r"\bbons? plans?\b",
    r"\bcodes? promo\b",
    r"\bvente flash\b",
]
# Rubriques : des regex aussi (sur la rubrique en minuscules sans accents).
# Attention aux préfixes trop courts : « publi » attraperait « Santé publique ».
RUBRIQUES_PUBLI = [r"\bpartenaires?\b", r"\bsponsorise", r"\bpubli[ -]?(reportage|redactionnel)", r"\bbons? plans?\b", r"^shopping$"]

MOTIFS_HOROSCOPE = [r"\bhoroscope", r"\bastrolog", r"\bsignes? (du zodiaque|astrologiques?)\b"]
RUBRIQUES_HOROSCOPE = [r"\bhoroscope", r"\bastrologie\b"]

MOTIFS_PROGRAMME_TV = [
    r"\bprogrammes? (tv|tele|television)\b",
    r"\bque regarder (ce soir|a la tele|a la television)\b",
    r"\b(ce soir|cette semaine) (a la tele|a la television|sur votre petit ecran)\b",
]
RUBRIQUES_PROGRAMME_TV = [r"\bprogrammes? (tv|tele)\b", r"\bguide tv\b"]

MOTIFS_JEUX = [
    r"\bjeux?[ -]concours\b",
    r"\b(places?|lots?|invitations?|cadeaux?|billets?) a gagner\b",
    r"\btentez de (gagner|remporter)\b",
    r"\bresultats? (du |de l |de )?(loto|euro ?millions|keno|amigo|eurodreams)\b",
    r"\btirages? (du |de l |de )?(loto|euro ?millions|keno|eurodreams)\b",
    r"\bmots (fleches|croises|meles)\b",
    r"\bsudoku\b",
    r"^quiz\b",
]
RUBRIQUES_JEUX = [r"^jeux( et loisirs)?$", r"\bjeux?[ -]concours\b", r"\bloterie\b"]  # pas « Jeux olympiques »

# Rubriques « magazine » hors actualité (observées chez EBRA : « Magazine Lifestyle »,
# « Magazine Tourisme et Patrimoine »).
RUBRIQUES_MAGAZINE = [r"^magazine\b"]

SEUIL_JACCARD = 0.8          # titres d'une même source à 80 % de mots communs = même article
FENETRE_DOUBLONS = timedelta(hours=48)  # on ne compare qu'aux articles gardés récemment
MOTS_MIN_JACCARD = 4         # en dessous, deux titres courts se ressemblent trop par hasard


# =========================================================================== CONTEXTE

@dataclass
class Contexte:
    """Ce que les règles de doublons ont besoin de connaître : les articles déjà gardés.

    Alimenté avec les articles propres des 48 dernières heures (base), puis avec
    chaque article gardé pendant le passage (ordre chronologique de publication).
    """

    maintenant: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # cle_titre -> source qui l'a publiée en premier
    cles: dict[str, str] = field(default_factory=dict)
    # source -> [(cle_titre, ensemble de mots)]
    par_source: dict[str, list[tuple[str, frozenset]]] = field(default_factory=dict)

    def garder(self, source: str, cle: str) -> None:
        self.cles.setdefault(cle, source)
        self.par_source.setdefault(source, []).append((cle, frozenset(cle.split())))


# =========================================================================== RÈGLES

@dataclass(frozen=True)
class Regle:
    id: str
    description: str
    test: Callable[[dict, Contexte], Optional[str]]


def _texte(*morceaux: Optional[str]) -> str:
    return sans_accents(" ".join(m for m in morceaux if m)).lower()


def _cherche(motifs: list[str], texte: str) -> Optional[str]:
    for motif in motifs:
        m = re.search(motif, texte)
        if m:
            return m.group(0)
    return None


def _par_motifs(motifs: list[str], rubriques: list[str], sur_extrait: bool = False):
    """Fabrique une règle « motif dans le titre (ou l'extrait), ou rubrique connue »."""

    def test(a: dict, _ctx: Contexte) -> Optional[str]:
        rubrique = _texte(a.get("rubrique"))
        r = _cherche(rubriques, rubrique)
        if r:
            return f"rubrique « {a['rubrique']} »"
        m = _cherche(motifs, _texte(a["titre"], a.get("extrait") if sur_extrait else None))
        return f"motif « {m} »" if m else None

    return test


def titre_vide(a: dict, _ctx: Contexte) -> Optional[str]:
    return "titre vide après nettoyage" if not a["titre"].strip() else None


def date_future(a: dict, ctx: Contexte) -> Optional[str]:
    if not a.get("publie_le"):
        return None
    publie = datetime.fromisoformat(a["publie_le"])
    if publie > ctx.maintenant + TOLERANCE_DATE_FUTURE:
        return f"publié le {a['publie_le']}, après le traitement ({ctx.maintenant:%Y-%m-%d %H:%M} UTC)"
    return None


def encodage_casse(a: dict, _ctx: Contexte) -> Optional[str]:
    """Ce que reparer_encodage n'a pas su réparer : la gazette ne peut pas l'afficher."""
    for champ in ("titre", "extrait"):
        texte = a.get(champ) or ""
        if "�" in texte:
            return f"caractère de remplacement « � » dans le {champ}"
        m = _MOJIBAKE.search(texte)
        if m:
            return f"séquence « {m.group(0)} » dans le {champ}"
    return None


def doublon_exact(a: dict, ctx: Contexte) -> Optional[str]:
    """Même source, même titre (clé de comparaison), lien différent : republication."""
    for cle, _ in ctx.par_source.get(a["source"], []):
        if cle == a["cle_titre"]:
            return "même titre déjà gardé pour cette source"
    return None


def reprise_identique(a: dict, ctx: Contexte) -> Optional[str]:
    """Même titre mot pour mot chez une autre source : dépêche d'agence ou contenu de groupe
    (ex. EBRA : L'Est Républicain / Le Dauphiné Libéré). On garde la première publiée."""
    premiere = ctx.cles.get(a["cle_titre"])
    if premiere and premiere != a["source"]:
        return f"titre identique déjà publié par {premiere}"
    return None


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def doublon_approximatif(a: dict, ctx: Contexte) -> Optional[str]:
    """Même source, titre retouché (Jaccard sur les mots >= SEUIL_JACCARD).

    Volontairement limité à UNE source : deux sources différentes qui titrent presque
    pareil sur le même événement, c'est le signal du regroupement (TD 5), pas un doublon.
    """
    mots = frozenset(a["cle_titre"].split())
    if len(mots) < MOTS_MIN_JACCARD:
        return None
    for cle, autres in ctx.par_source.get(a["source"], []):
        j = jaccard(mots, autres)
        if j >= SEUIL_JACCARD:
            return f"Jaccard {j:.2f} avec « {cle[:80]} »"
    return None


REGLES: list[Regle] = [
    Regle("R01_titre_vide", "Titre vide après nettoyage", titre_vide),
    Regle("R02_date_future", "Date de publication dans le futur (au-delà d'1 h de marge)", date_future),
    Regle("R03_encodage", "Encodage cassé que la normalisation n'a pas pu réparer", encodage_casse),
    Regle("R04_publi", "Publi-reportage, contenu partenaire, bons plans",
          _par_motifs(MOTIFS_PUBLI, RUBRIQUES_PUBLI, sur_extrait=True)),
    Regle("R05_horoscope", "Horoscope, astrologie", _par_motifs(MOTIFS_HOROSCOPE, RUBRIQUES_HOROSCOPE)),
    Regle("R06_programme_tv", "Programmes TV", _par_motifs(MOTIFS_PROGRAMME_TV, RUBRIQUES_PROGRAMME_TV)),
    Regle("R07_jeux", "Jeux, concours à gagner, tirages, grilles", _par_motifs(MOTIFS_JEUX, RUBRIQUES_JEUX)),
    Regle("R08_magazine", "Rubriques magazine hors actualité", _par_motifs([], RUBRIQUES_MAGAZINE)),
    Regle("R09_doublon_exact", "Même titre, même source, autre lien", doublon_exact),
    Regle("R10_reprise", "Titre identique chez une autre source (agence, groupe)", reprise_identique),
    Regle("R11_doublon_approx", "Titre presque identique dans la même source", doublon_approximatif),
]


def appliquer(article: dict, ctx: Contexte) -> Optional[tuple[str, str]]:
    """(id_regle, detail) de la première règle qui rejette, ou None si l'article est gardé."""
    for r in REGLES:
        detail = r.test(article, ctx)
        if detail:
            return r.id, detail
    return None
