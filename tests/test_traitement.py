"""Traitement de bout en bout sur une base SQLite jetable : brut -> articles_propres | rejets."""

import json
from datetime import datetime, timezone

from sqlalchemy import text

from collect.items import identifiant
from collect.stockage import StockageBrut
from qualite.traitement import chemin_absolu, traiter

MAINTENANT = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def brut(source, titre, publie_le="2026-11-12T08:00:00+00:00", rubrique=None, n=0):
    lien = f"https://ex.test/{source}/{n}"
    return {
        "id": identifiant(source, lien), "source": source, "lien": lien, "titre": titre,
        "resume": "<p>Un résumé</p>", "publie_le": publie_le,
        "recupere_le": "2026-11-12T09:30:00+00:00", "rubrique": rubrique, "payload": {},
    }


def remplir(cfg):
    s = StockageBrut(cfg.dossier_bruts)
    articles = [
        brut("Le Monde", "Budget 2027 : l'Assemblée adopte la première partie", n=1),
        brut("Le Monde", "Horoscope du jour : Bélier", n=2),
        brut("Le Dauphiné Libéré", "Info EBRA. La France veut son autonomie alimentaire",
             publie_le="2026-11-12T07:00:00+00:00", n=3),
        brut("L'Est Républicain", "Info EBRA. La France veut son autonomie alimentaire",
             publie_le="2026-11-12T07:05:00+00:00", n=4),
        brut("Le Figaro", "Animaux. Clémentine et chiens", rubrique="Magazine Lifestyle", n=5),
    ]
    for a in articles:
        s.ecrire(a)
    return articles


def test_traitement_repartit_et_trace(cfg, engine):
    articles = remplir(cfg)
    b = traiter(cfg, engine, maintenant=MAINTENANT)

    assert (b.entres, b.gardes, b.rejetes) == (5, 2, 3)
    assert dict(b.par_regle) == {"R05_horoscope": 1, "R10_reprise": 1, "R08_magazine": 1}
    assert b.par_source["L'Est Républicain"]["rejetes"] == 1

    with engine.connect() as c:
        propres = {r.id: r for r in c.execute(text("SELECT * FROM articles_propres"))}
        rejets = {r.id: r for r in c.execute(text("SELECT * FROM rejets"))}

    assert propres.keys().isdisjoint(rejets.keys())
    assert len(propres) + len(rejets) == len(articles)
    # la reprise EBRA : on garde la première publiée (Le Dauphiné), on rejette l'autre
    dauphine, est = articles[2]["id"], articles[3]["id"]
    assert dauphine in propres and rejets[est].regle == "R10_reprise"

    # traçabilité : article propre -> fichier brut -> même id, même source
    p = propres[articles[0]["id"]]
    original = json.loads(chemin_absolu(p.chemin_brut).read_text(encoding="utf-8"))
    assert original["id"] == p.id and original["source"] == p.source
    assert p.extrait == "Un résumé"  # HTML retiré


def test_deux_passages_zero_doublon(cfg, engine):
    remplir(cfg)
    traiter(cfg, engine, maintenant=MAINTENANT)
    b2 = traiter(cfg, engine, maintenant=MAINTENANT)
    assert (b2.entres, b2.deja_traites) == (0, 5)

    with engine.connect() as c:
        n = c.execute(text("SELECT (SELECT COUNT(*) FROM articles_propres) + (SELECT COUNT(*) FROM rejets)")).scalar()
    assert n == 5


def test_doublon_avec_un_passage_precedent(cfg, engine):
    s = StockageBrut(cfg.dossier_bruts)
    s.ecrire(brut("Le Monde", "Grève SNCF : le trafic très perturbé ce jeudi en Île-de-France", n=1))
    traiter(cfg, engine, maintenant=MAINTENANT)

    # au passage suivant, le même article republié sous un autre lien
    s.ecrire(brut("Le Monde", "Grève SNCF : le trafic très perturbé ce jeudi en Île-de-France", n=2))
    b = traiter(cfg, engine, maintenant=MAINTENANT)
    assert dict(b.par_regle) == {"R09_doublon_exact": 1}
