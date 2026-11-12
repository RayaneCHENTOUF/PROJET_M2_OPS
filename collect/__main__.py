"""Ligne de commande du collecteur.

    python -m collect migrer          applique les migrations sql/migrations/V*.sql
    python -m collect sync-sources    aligne la table sources sur sources/sources.csv
    python -m collect collecter       un passage sur toutes les sources actives
    python -m collect tout            les trois a la suite
    python -m collect etat            etat des sources (derniere reussite, echecs)
    python -m collect verifier        code retour 1 si une source dort (alerte Airflow)
    python -m collect reveiller NOM   sort une source du sommeil
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import db
from .collecteur import collecter_tout
from .config import Config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m collect", description="Collecteur de La Gazette")
    sub = p.add_subparsers(dest="cmd", required=True)
    for nom in ("migrer", "sync-sources", "collecter", "tout", "etat", "verifier"):
        sub.add_parser(nom)
    sub.add_parser("reveiller").add_argument("nom")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.depuis_env()
    engine = db.moteur(cfg.database_url)
    log = logging.getLogger("collect")

    if args.cmd in ("migrer", "tout"):
        log.info("migrations appliquees : %s", db.migrer(engine, cfg.dossier_migrations) or "aucune")
    if args.cmd in ("sync-sources", "tout"):
        log.info("sources : %s", db.synchroniser_sources(engine, cfg.fichier_sources))
    if args.cmd in ("collecter", "tout"):
        bilans = collecter_tout(cfg, engine)
        interrogees = [b for b in bilans if b.statut != "sommeil"]
        if interrogees and all(b.statut == "echec" for b in interrogees):
            log.error("toutes les sources ont echoue : probleme reseau ?")
            return 1

    if args.cmd == "etat":
        print(f"{'source':<20} {'actif':<6} {'echecs':<7} {'derniere reussite':<20} erreur")
        for s in db.lister_sources(engine):
            print(f"{s.nom:<20} {str(s.actif):<6} {s.echecs_consecutifs:<7} "
                  f"{s.derniere_reussite or '-':<20} {s.derniere_erreur or ''}")
    if args.cmd == "verifier":
        endormies = [s for s in db.sources_actives(engine) if s.echecs_consecutifs >= cfg.seuil_sommeil]
        for s in endormies:
            log.error('source="%s" EN SOMMEIL (%d echecs) : %s', s.nom, s.echecs_consecutifs, s.derniere_erreur)
        if endormies:
            return 1
        log.info("aucune source en sommeil")
    if args.cmd == "reveiller":
        if not db.reveiller(engine, args.nom):
            log.error("source inconnue : %s", args.nom)
            return 1
        log.info("source reveillee : %s", args.nom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
