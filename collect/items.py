"""Lecture d'un flux (RSS 2.0 ou Atom) et mise au format `articles_bruts`.

feedparser fait le travail difficile : il unifie les noms de champs
(pubDate/updated -> published_parsed/updated_parsed, description/summary -> summary,
guid/id -> id), detecte l'encodage et convertit les dates en UTC.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urldefrag

import feedparser

from .reseau import ErreurPermanente

# Champs du payload jamais conserves (voir docs/donnees.md, « Ce qu'on ne stocke pas ») :
# - content, summary, summary_detail : peuvent contenir le texte integral (droits voisins) ;
#   le resume, lui, est garde TRONQUE dans le champ `resume` de l'article ;
# - auteurs, contributeurs, credits photo : noms de personnes (donnees personnelles) ;
# - media_* : liens vers les images, dont on n'a pas le droit ni le besoin.
CHAMPS_EXCLUS = {
    "content", "summary", "summary_detail",
    "author", "authors", "author_detail", "contributors", "publisher", "publisher_detail",
    "credit", "media_credit",
    "media_content", "media_thumbnail",
}


def lire_flux(contenu: bytes, content_type: Optional[str] = None):
    en_tetes = {"content-type": content_type} if content_type else {}
    flux = feedparser.parse(contenu, response_headers=en_tetes)
    if flux.bozo and not flux.entries:
        raise ErreurPermanente(f"flux illisible : {flux.get('bozo_exception')!r}")
    if not flux.get("version") and not flux.entries:
        raise ErreurPermanente("la reponse n'est ni du RSS ni de l'Atom")
    return flux


def identifiant(source: str, lien: str) -> str:
    """Identifiant stable derive de la cle naturelle (source, lien)."""
    return hashlib.sha256(f"{source}\n{lien}".encode("utf-8")).hexdigest()[:32]


def _iso(t: Optional[time.struct_time]) -> Optional[str]:
    if not t:
        return None
    return datetime(*t[:6], tzinfo=timezone.utc).isoformat()


def _jsonable(v: Any) -> Any:
    if isinstance(v, time.struct_time):
        return _iso(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def normaliser_lien(lien: str) -> str:
    return urldefrag(lien.strip())[0]


def vers_article(entree, source: str, recupere_le: str, taille_max_resume: int = 1000) -> Optional[dict]:
    """Transforme un item du flux en article brut. None si l'item n'a pas de lien exploitable."""
    lien = entree.get("link") or ""
    if not lien.startswith(("http://", "https://")):
        guid = entree.get("id") or ""  # certains flux ne mettent l'URL que dans le guid
        lien = guid if guid.startswith(("http://", "https://")) else ""
    if not lien:
        return None
    lien = normaliser_lien(lien)

    resume = entree.get("summary") or ""
    if not resume and entree.get("content"):
        resume = entree["content"][0].get("value", "")

    tags = entree.get("tags") or []
    rubrique = next((t.get("term") or t.get("label") for t in tags if t.get("term") or t.get("label")), None)

    return {
        "id": identifiant(source, lien),
        "source": source,
        "lien": lien,
        "titre": (entree.get("title") or "").strip(),
        "resume": resume[:taille_max_resume],
        "publie_le": _iso(entree.get("published_parsed") or entree.get("updated_parsed")),
        "recupere_le": recupere_le,
        "rubrique": rubrique,
        "payload": {k: _jsonable(v) for k, v in entree.items() if k not in CHAMPS_EXCLUS},
    }
