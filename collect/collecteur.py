"""Un passage de collecte : toutes les sources actives, une ligne de journal par source."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.engine import Engine

from . import db
from .config import Config
from .reseau import ErreurCollecte, ErreurPermanente, nouvelle_session, telecharger
from .items import lire_flux, vers_article
from .stockage import StockageBrut

log = logging.getLogger("collecte")


@dataclass
class Bilan:
    source: str
    statut: str            # ok | inchange | echec | sommeil (deja endormie, non interrogee)
    http: Optional[int] = None
    recus: int = 0
    ecrits: int = 0
    ignores: int = 0
    duree: float = 0.0
    essais: int = 0
    echecs: int = 0
    erreur: Optional[str] = None

    def ligne(self) -> str:
        l = (f'source="{self.source}" statut={self.statut} http={self.http or "-"} '
             f"recus={self.recus} ecrits={self.ecrits} ignores={self.ignores} "
             f"duree={self.duree:.2f}s essais={self.essais} echecs_consecutifs={self.echecs}")
        return l + (f' erreur="{self.erreur}"' if self.erreur else "")


def collecter_source(
    source: db.Source,
    engine: Engine,
    stockage: StockageBrut,
    cfg: Config,
    session=None,
    dormir: Callable[[float], None] = time.sleep,
) -> Bilan:
    if source.echecs_consecutifs >= cfg.seuil_sommeil:
        b = Bilan(source.nom, "sommeil", echecs=source.echecs_consecutifs, erreur=source.derniere_erreur)
        log.warning(b.ligne())
        return b

    session = session or nouvelle_session(cfg)
    debut = time.monotonic()
    b = Bilan(source.nom, "ok")
    try:
        rep = telecharger(session, source.url_flux, cfg, source.etag, source.last_modified, dormir)
        b.http, b.essais = rep.http, rep.essais
        if rep.contenu is not None:
            flux = lire_flux(rep.contenu, rep.content_type)
            recupere_le = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connus = stockage.ids_connus(source.nom)
            for entree in flux.entries:
                b.recus += 1
                article = vers_article(entree, source.nom, recupere_le, cfg.taille_max_resume)
                if article is None or article["id"] in connus:
                    b.ignores += 1
                    continue
                stockage.ecrire(article)
                connus.add(article["id"])
                b.ecrits += 1
        else:
            b.statut = "inchange"
        db.noter_reussite(engine, source.nom, rep.etag, rep.last_modified)
    except Exception as e:  # une source qui casse ne doit jamais arreter les autres
        if isinstance(e, ErreurCollecte):
            b.http, b.essais = e.http, e.essais
        else:
            log.exception("erreur inattendue sur %s", source.nom)
        b.statut = "echec"
        b.erreur = f"{type(e).__name__}: {e}" if not isinstance(e, ErreurCollecte) else str(e)
        b.echecs = db.noter_echec(engine, source.nom, b.erreur)
    b.duree = time.monotonic() - debut

    if b.statut == "echec":
        log.error(b.ligne())
        if b.echecs >= cfg.seuil_sommeil:
            log.error('source="%s" MISE EN SOMMEIL apres %d echecs consecutifs : '
                      "corriger puis `python -m collect reveiller \"%s\"`", source.nom, b.echecs, source.nom)
    else:
        log.info(b.ligne())
    return b


def collecter_tout(cfg: Config, engine: Engine, dormir: Callable[[float], None] = time.sleep) -> list[Bilan]:
    """Sources traitees l'une apres l'autre : jamais de rafale sur un meme editeur."""
    stockage = StockageBrut(cfg.dossier_bruts)
    session = nouvelle_session(cfg)
    bilans = [collecter_source(s, engine, stockage, cfg, session, dormir) for s in db.sources_actives(engine)]

    tot = {k: sum(getattr(b, k) for b in bilans) for k in ("recus", "ecrits", "ignores")}
    par_statut = {st: sum(b.statut == st for b in bilans) for st in ("ok", "inchange", "echec", "sommeil")}
    log.info("bilan sources=%d %s recus=%d ecrits=%d ignores=%d",
             len(bilans), " ".join(f"{k}={v}" for k, v in par_statut.items()), *tot.values())
    return bilans
