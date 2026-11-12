"""Ligne de commande du traitement qualité.

    python -m qualite traiter [--jours N]       un passage : brut des N derniers jours -> articles_propres | rejets
    python -m qualite controler [--seuil-rejet X] contrôles de qualité : métriques publiées,
                                                  code retour 1 si un fait est faux (alerte Airflow)
    python -m qualite rapport [--date AAAA-MM-JJ] rapport du jour -> rapports/qualite-AAAA-MM-JJ.md
                                                  (réécrit à chaque passage ; --date pour régénérer un autre jour)
    python -m qualite retention [--simulation]   applique la rétention (bruts archivés à 30 j, rejets 30 j,
                                                  articles propres 1 an) ; --simulation n'efface rien
"""

from __future__ import annotations

import argparse
from datetime import date
import logging
import os
import sys

from collect import db
from collect.config import Config

from .controles import SEUIL_TAUX_REJET, controler
from .rapport import generer
from . import retention
from .traitement import traiter


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m qualite", description="Traitement qualité de La Gazette")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("traiter")
    t.add_argument("--jours", type=int, default=2,
                   help="partitions brutes relues (défaut 2 : aujourd'hui et hier, pour passer minuit)")
    c = sub.add_parser("controler")
    c.add_argument("--seuil-rejet", type=float,
                   default=float(os.getenv("GAZETTE_SEUIL_REJET", SEUIL_TAUX_REJET)),
                   help=f"taux de rejet maximal par source (défaut {SEUIL_TAUX_REJET}, ou GAZETTE_SEUIL_REJET)")
    r = sub.add_parser("rapport")
    r.add_argument("--date", type=date.fromisoformat, default=None, help="jour à (re)générer (défaut : aujourd'hui, UTC)")
    ret = sub.add_parser("retention")
    ret.add_argument("--simulation", action="store_true", help="affiche ce qui serait archivé ou supprimé, sans rien toucher")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.depuis_env()
    engine = db.moteur(cfg.database_url)
    db.migrer(engine, cfg.dossier_migrations)  # les tables doivent exister, même lancé seul

    if args.cmd == "traiter":
        traiter(cfg, engine, jours=args.jours)
    if args.cmd == "controler":
        return 0 if controler(engine, seuil_rejet=args.seuil_rejet).ok else 1
    if args.cmd == "rapport":
        generer(engine, cfg.dossier_rapports, jour=args.date)
    if args.cmd == "retention":
        retention.appliquer(cfg, engine, simulation=args.simulation)
    return 0


if __name__ == "__main__":
    sys.exit(main())
