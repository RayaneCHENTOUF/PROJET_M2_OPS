"""Telechargement poli d'un flux : User-Agent, en-tetes conditionnels, timeouts, backoff."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

# Codes pour lesquels un nouvel essai a du sens : le serveur est momentanement indisponible.
CODES_TRANSITOIRES = {408, 425, 429, 500, 502, 503, 504}


class ErreurCollecte(Exception):
    """Echec de collecte d'une source."""

    def __init__(self, message: str, http: Optional[int] = None, essais: int = 1):
        super().__init__(message)
        self.http = http
        self.essais = essais


class ErreurTransitoire(ErreurCollecte):
    """5xx, 429, timeout, connexion refusee : on reessaie avec une attente croissante."""


class ErreurPermanente(ErreurCollecte):
    """404, 410, 403, flux illisible : reessayer tout de suite ne changera rien."""


@dataclass
class Reponse:
    http: int
    contenu: Optional[bytes]          # None si 304 (rien de neuf)
    content_type: Optional[str]
    etag: Optional[str]
    last_modified: Optional[str]
    essais: int


def nouvelle_session(cfg: Config) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": cfg.user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1",
    })
    return s


def _attente(cfg: Config, essai: int, retry_after: Optional[str]) -> float:
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), cfg.backoff_max)
    base = cfg.backoff_base * (2 ** (essai - 1))
    return min(base + random.uniform(0, base * 0.1), cfg.backoff_max)


def telecharger(
    session: requests.Session,
    url: str,
    cfg: Config,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    dormir: Callable[[float], None] = time.sleep,
) -> Reponse:
    en_tetes = {}
    if etag:
        en_tetes["If-None-Match"] = etag
    if last_modified:
        en_tetes["If-Modified-Since"] = last_modified

    derniere: ErreurCollecte = ErreurTransitoire("aucun essai")
    for essai in range(1, cfg.tentatives + 1):
        retry_after = None
        try:
            r = session.get(url, headers=en_tetes, timeout=(cfg.timeout_connexion, cfg.timeout_lecture))
        except requests.Timeout as e:
            derniere = ErreurTransitoire(f"timeout : {e}", essais=essai)
        except requests.ConnectionError as e:
            derniere = ErreurTransitoire(f"connexion impossible : {e}", essais=essai)
        except requests.RequestException as e:  # URL invalide, schema inconnu...
            raise ErreurPermanente(f"requete invalide : {e}", essais=essai) from e
        else:
            if r.status_code == 304:
                return Reponse(304, None, None, etag, last_modified, essai)
            if 200 <= r.status_code < 300:
                return Reponse(
                    r.status_code, r.content, r.headers.get("Content-Type"),
                    r.headers.get("ETag"), r.headers.get("Last-Modified"), essai,
                )
            if r.status_code in CODES_TRANSITOIRES:
                derniere = ErreurTransitoire(f"HTTP {r.status_code}", http=r.status_code, essais=essai)
                retry_after = r.headers.get("Retry-After")
            else:
                raise ErreurPermanente(f"HTTP {r.status_code}", http=r.status_code, essais=essai)

        if essai < cfg.tentatives:
            attente = _attente(cfg, essai, retry_after)
            log.warning("%s : %s (essai %d/%d), nouvel essai dans %.1f s",
                        url, derniere, essai, cfg.tentatives, attente)
            dormir(attente)
    raise derniere
