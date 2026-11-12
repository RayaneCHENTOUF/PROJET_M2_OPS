"""Normalisation des articles bruts : une fonction pure par transformation.

Chaque fonction prend une valeur et en rend une nouvelle, sans effet de bord ni
accès réseau ou disque : elle se teste seule (tests/test_normalisation.py).

Ordre appliqué par `normaliser` sur le titre et le résumé :
    reparer_encodage -> decoder_entites -> retirer_html -> normaliser_espaces
(l'extrait est ensuite borné ; la date est ramenée en UTC).

Pourquoi cet ordre :
- l'encodage d'abord : tant que « Ã© » n'est pas réparé, rien d'autre n'est fiable ;
- les entités avant le HTML : certains flux échappent leurs balises (« &lt;p&gt; »),
  qui ne deviennent des balises retirables qu'une fois décodées ;
- les espaces en dernier : retirer une balise laisse des espaces en trop.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

TAILLE_EXTRAIT = 300  # caractères : un extrait court, que l'on a le droit de reprendre

# Mojibake : un caractère UTF-8 (2 ou 3 octets) relu en Windows-1252 devient 2 ou 3 caractères.
# Octets de continuation (0x80-0xBF) tels qu'ils apparaissent une fois relus en cp1252 / latin-1 :
_SUITE = "[\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]"
# On ne vise que les débuts de séquence fréquents en français, pour ne pas toucher un texte sain :
#   Â (C2 : espace insécable, « » °), Ã (C3 : é è à ç ê ô É…), Å (C5 : œ Œ), â (E2 : ’ “ ” … – — €).
_MOJIBAKE = re.compile(f"[ÂÃÅ]{_SUITE}|â{_SUITE}{{2}}")
# « à » cassé = « Ã » + espace insécable, souvent déjà devenue une espace normale : irréparable
# octet par octet, mais « Ã » suivi d'une espace n'existe dans aucun texte français sain.
_A_GRAVE_CASSE = re.compile(r"Ã(?=\s)")
_BALISE = re.compile(r"<[/!]?[a-zA-Z][^<>]*>")  # « 3 < 5 » n'est pas une balise
_BLOC_INVISIBLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_INVISIBLES = dict.fromkeys(map(ord, "​‌‍⁠﻿\xad"))  # zero-width, BOM, césure


def reparer_encodage(texte: str) -> str:
    """« Ã©lection » -> « élection ». Un texte sain est rendu tel quel.

    La réparation se fait séquence par séquence : un caractère irréparable ailleurs
    dans le texte (emoji, « Ã » isolé…) n'empêche pas de réparer le reste.
    Une séquence qui ne redonne pas un UTF-8 valide est laissée telle quelle.
    Deux tours au plus, pour le double encodage (« ÃƒÂ© » -> « Ã© » -> « é »).
    """
    for _ in range(2):
        repare = _MOJIBAKE.sub(_reparer_sequence, texte)
        if repare == texte:
            break
        texte = repare
    return _A_GRAVE_CASSE.sub("à", texte)


def _reparer_sequence(m: re.Match) -> str:
    sequence = m.group(0)
    for codec in ("cp1252", "latin-1"):
        try:
            return sequence.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return sequence


def decoder_entites(texte: str) -> str:
    """« &amp; » -> « & », « &eacute; » -> « é ». Deux passes pour « &amp;eacute; »."""
    for _ in range(2):
        decode = html.unescape(texte)
        if decode == texte:
            break
        texte = decode
    return texte


def retirer_html(texte: str) -> str:
    """Retire les balises (et le contenu des <script>/<style>), remplacées par une espace."""
    texte = _BLOC_INVISIBLE.sub(" ", texte)
    return _BALISE.sub(" ", texte)


def normaliser_espaces(texte: str) -> str:
    """Forme Unicode NFC, caractères invisibles retirés, espaces (y compris insécables) réduites à une seule."""
    texte = unicodedata.normalize("NFC", texte).translate(_INVISIBLES)
    return " ".join(texte.split())  # split() sans argument couvre \xa0,  , \n, \t…


def nettoyer_texte(texte: Optional[str]) -> str:
    """Enchaîne les quatre transformations dans l'ordre documenté en tête de module."""
    if not texte:
        return ""
    return normaliser_espaces(retirer_html(decoder_entites(reparer_encodage(texte))))


def borner_extrait(texte: str, taille: int = TAILLE_EXTRAIT) -> str:
    """Coupe à `taille` caractères au plus, sur une fin de mot, et ajoute « … » si coupé."""
    if len(texte) <= taille:
        return texte
    coupe = texte[: taille - 1]
    if " " in coupe and not texte[taille - 1].isspace():
        coupe = coupe.rsplit(" ", 1)[0]
    return coupe.rstrip(" ,;:.-–—") + "…"


def date_utc(valeur: Optional[str]) -> Optional[str]:
    """Date ISO 8601 -> ISO 8601 en UTC (« 2026-11-12T08:00:00+00:00 »).

    Sans fuseau, la date est supposée déjà en UTC (c'est ce que produit la collecte).
    Valeur absente ou illisible -> None (la règle « sans date » décidera quoi en faire).
    """
    if not valeur:
        return None
    try:
        d = datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# Préfixe éditorial en tête de titre : « VIDÉOS. », « Isère. », « Info EBRA. », « Etats-Unis. »
# (une majuscule, 2 à 30 caractères sans ponctuation forte, un point, une espace).
# « M. Macron » n'est pas touché : il faut au moins 2 caractères avant le point.
_PREFIXE = re.compile(r"^[A-ZÀ-Ý][^.!?:«»\"]{1,29}\.\s+")


def sans_accents(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()


def cle_titre(titre: str) -> str:
    """Forme comparable d'un titre, pour le dédoublonnage (jamais affichée).

    « VIDÉOS. New York : un immeuble s’effondre » -> « new york un immeuble s effondre »
    Préfixes éditoriaux retirés (deux au plus : « Isère. Info EBRA. … »),
    minuscules, sans accents, ponctuation remplacée par des espaces.
    """
    for _ in range(2):
        titre = _PREFIXE.sub("", titre)
    titre = re.sub(r"[\W_]+", " ", titre.lower())  # ponctuation d'abord : « s’effondre » -> « s effondre »
    return " ".join(sans_accents(titre).split())


def normaliser(article: dict, taille_extrait: int = TAILLE_EXTRAIT) -> dict:
    """Article brut -> article normalisé (nouveau dict, le brut n'est pas modifié).

    Ne garde que ce qu'on a le droit de republier : titre, extrait court, lien, dates,
    source, rubrique. Le `payload` brut n'est pas recopié : il reste dans le fichier
    d'origine, retrouvable par `id` (traçabilité).
    """
    rubrique = nettoyer_texte(article.get("rubrique")) or None
    titre = nettoyer_texte(article.get("titre"))
    return {
        "id": article["id"],
        "source": article["source"],
        "lien": article["lien"],
        "titre": titre,
        "cle_titre": cle_titre(titre),
        "extrait": borner_extrait(nettoyer_texte(article.get("resume")), taille_extrait),
        "publie_le": date_utc(article.get("publie_le")),
        "recupere_le": date_utc(article.get("recupere_le")),
        "rubrique": rubrique,
    }
