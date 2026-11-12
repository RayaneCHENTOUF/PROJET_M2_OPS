"""Stockage brut : un fichier JSON par article.

    data/articles_bruts/{YYYY-MM-DD}/{source-slug}/{id}.json

- partition par date de recuperation : le traitement (TD 4) relit un jour precis ;
- un fichier par article : l'ecriture est atomique et le dedoublonnage se fait
  sur l'existence de l'id (derive de (source, lien)), tous jours confondus.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path


def slug(nom: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


class StockageBrut:
    def __init__(self, racine: Path):
        self.racine = Path(racine)

    def ids_connus(self, source: str) -> set[str]:
        return {p.stem for p in self.racine.glob(f"*/{slug(source)}/*.json")}

    def ecrire(self, article: dict) -> Path:
        dossier = self.racine / article["recupere_le"][:10] / slug(article["source"])
        dossier.mkdir(parents=True, exist_ok=True)
        cible = dossier / f"{article['id']}.json"
        tmp = cible.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cible)
        return cible

    def compter(self) -> int:
        return sum(1 for _ in self.racine.glob("*/*/*.json"))
