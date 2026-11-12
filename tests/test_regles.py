"""Règles d'exclusion : chaque règle rejette ses cas ET laisse passer les vrais articles qui lui ressemblent.

Les cas sont dans tests/fixtures/articles_pollues.json (pollueurs réels observés + attendus,
voir docs/qualite.md). C'est ce test que la CI lance : il teste le CODE des règles sur une
fixture ; les FAITS sur les vraies données sont vérifiés par les contrôles de qualité.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qualite.normalisation import normaliser
from qualite.regles import REGLES, Contexte, appliquer, jaccard

MAINTENANT = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CAS = json.loads((Path(__file__).parent / "fixtures" / "articles_pollues.json").read_text(encoding="utf-8"))


def article(titre, source="Le Monde", lien=None, resume="", rubrique=None,
            publie_le="2026-11-12T08:00:00+00:00"):
    lien = lien or f"https://ex.test/{abs(hash((source, titre)))}"
    return normaliser({
        "id": str(abs(hash(lien)))[:32], "source": source, "lien": lien, "titre": titre, "resume": resume,
        "publie_le": publie_le, "recupere_le": "2026-11-12T09:30:00+00:00", "rubrique": rubrique,
    })


@pytest.mark.parametrize("cas", CAS, ids=[c["cas"] for c in CAS])
def test_fixture(cas):
    a = article(cas["titre"], resume=cas.get("resume", ""), rubrique=cas.get("rubrique"),
                publie_le=cas.get("publie_le", "2026-11-12T08:00:00+00:00"))
    decision = appliquer(a, Contexte(maintenant=MAINTENANT))
    assert (decision[0] if decision else None) == cas["attendu"], decision


def test_chaque_regle_a_un_identifiant_unique_et_documente():
    ids = [r.id for r in REGLES]
    assert len(ids) == len(set(ids))
    doc = (Path(__file__).parent.parent / "docs" / "qualite.md").read_text(encoding="utf-8")
    manquantes = [i for i in ids if i not in doc]
    assert not manquantes, f"règles absentes de docs/qualite.md : {manquantes}"


# --- doublons : ils dépendent de ce qui a déjà été gardé (contexte) -----------

def _ctx_avec(*articles):
    ctx = Contexte(maintenant=MAINTENANT)
    for a in articles:
        ctx.garder(a["source"], a["cle_titre"])
    return ctx


def test_doublon_exact_meme_source_autre_lien():
    a1 = article("Grève SNCF : le trafic perturbé jeudi", lien="https://ex.test/a")
    a2 = article("Grève SNCF : le trafic perturbé jeudi", lien="https://ex.test/a?utm_source=rss")
    assert appliquer(a2, _ctx_avec(a1))[0] == "R09_doublon_exact"


def test_reprise_identique_autre_source():
    # observé : EBRA publie le même titre dans L'Est Républicain et Le Dauphiné Libéré
    titre = "Info EBRA. Lait, agrumes, volailles : la France se lance à la reconquête de son autonomie alimentaire"
    a1 = article(titre, source="Le Dauphiné Libéré")
    a2 = article(titre, source="L'Est Républicain")
    regle, detail = appliquer(a2, _ctx_avec(a1))
    assert regle == "R10_reprise" and "Le Dauphiné Libéré" in detail


def test_reprise_ignore_les_prefixes_editoriaux():
    a1 = article("Isère. Deux gardes d'honneur s'effondrent devant Emmanuel Macron", source="Le Dauphiné Libéré")
    a2 = article("Paris. Deux gardes d'honneur s'effondrent devant Emmanuel Macron", source="L'Est Républicain")
    assert appliquer(a2, _ctx_avec(a1))[0] == "R10_reprise"


def test_doublon_approximatif_meme_source():
    a1 = article("New York : un immeuble s'effondre à Brooklyn, au moins deux morts et 17 blessés")
    a2 = article("New York : un immeuble s'effondre à Brooklyn, au moins deux morts et 18 blessés")
    regle, detail = appliquer(a2, _ctx_avec(a1))
    assert regle == "R11_doublon_approx" and "Jaccard" in detail


def test_titres_proches_de_sources_differentes_sont_gardes():
    # observé : Libération / Sud Ouest sur le même événement -> c'est le travail du TD 5, pas un rejet
    a1 = article("Un immeuble s’effondre à New York, au moins deux morts et 17 blessés", source="Libération")
    a2 = article("VIDÉOS. New York : un immeuble s’effondre à Brooklyn, au moins deux morts et 17 blessés",
                 source="Sud Ouest")
    assert appliquer(a2, _ctx_avec(a1)) is None


def test_titres_courts_pas_compares():
    a1 = article("Météo : orages")
    a2 = article("Météo : orages forts")
    assert appliquer(a2, _ctx_avec(a1)) is None


def test_jaccard():
    assert jaccard(frozenset("abc"), frozenset("abc")) == 1.0
    assert jaccard(frozenset("ab"), frozenset("cd")) == 0.0
    assert jaccard(frozenset("abcd"), frozenset("abce")) == 3 / 5
