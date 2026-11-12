"""Ligne de commande du traitement qualité.

    python -m qualite traiter [--jours N]   un passage : brut des N derniers jours -> articles_propres | rejets
"""

from __future__ import annotations

import argparse
import logging
import sys

from collect import db
from collect.config import Config

from .traitement import traiter


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m qualite", description="Traitement qualité de La Gazette")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("traiter")
    t.add_argument("--jours", type=int, default=2,
                   help="partitions brutes relues (défaut 2 : aujourd'hui et hier, pour passer minuit)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.depuis_env()
    engine = db.moteur(cfg.database_url)
    db.migrer(engine, cfg.dossier_migrations)  # la table propre doit exister, même lancé seul

    if args.cmd == "traiter":
        traiter(cfg, engine, jours=args.jours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
