"""Test « deux passages, zero doublon » + en-tetes conditionnels + formats RSS/Atom."""

import json

from collect import db
from collect.collecteur import collecter_tout
from collect.stockage import StockageBrut

from .conftest import ATOM, RSS, ecrire_sources

CHAMPS = {"id", "source", "lien", "titre", "resume", "publie_le", "recupere_le", "rubrique", "payload"}


def test_deux_passages_zero_doublon(cfg, engine, serveur):
    serveur.routes["/rss"] = (200, RSS, {"Content-Type": "application/rss+xml"})  # pas d'ETag : 200 a chaque fois
    ecrire_sources(cfg, ("Journal", serveur.url + "/rss"))
    db.synchroniser_sources(engine, cfg.fichier_sources)
    stockage = StockageBrut(cfg.dossier_bruts)

    [p1] = collecter_tout(cfg, engine)
    assert (p1.recus, p1.ecrits, p1.ignores) == (4, 2, 2)  # 1 doublon interne (#comments) + 1 sans lien
    n = stockage.compter()

    [p2] = collecter_tout(cfg, engine)
    assert serveur.appels("/rss") == 2                      # le flux a bien ete relu
    assert (p2.recus, p2.ecrits, p2.ignores) == (4, 0, 4)
    assert stockage.compter() == n == 2                     # le nombre d'articles ne bouge pas


def test_etag_evite_de_retelecharger(cfg, engine, serveur):
    serveur.routes["/rss"] = (200, RSS, {"ETag": '"v1"', "Last-Modified": "Tue, 10 Nov 2026 09:30:00 GMT"})
    ecrire_sources(cfg, ("Journal", serveur.url + "/rss"))
    db.synchroniser_sources(engine, cfg.fichier_sources)

    collecter_tout(cfg, engine)
    [p2] = collecter_tout(cfg, engine)

    _, en_tetes = serveur.requetes[-1]
    assert en_tetes["If-None-Match"] == '"v1"'
    assert en_tetes["If-Modified-Since"] == "Tue, 10 Nov 2026 09:30:00 GMT"
    assert "LaGazetteBot" in en_tetes["User-Agent"]
    assert p2.statut == "inchange" and p2.http == 304 and p2.recus == 0


def test_rss_et_atom_donnent_le_meme_format(cfg, engine, serveur):
    serveur.routes["/rss"] = (200, RSS, {})
    serveur.routes["/atom"] = (200, ATOM, {"Content-Type": "application/atom+xml"})
    ecrire_sources(cfg, ("Rss", serveur.url + "/rss"), ("Atom", serveur.url + "/atom"))
    db.synchroniser_sources(engine, cfg.fichier_sources)
    collecter_tout(cfg, engine)

    articles = {a["lien"]: a for a in
                (json.loads(p.read_text(encoding="utf-8")) for p in cfg.dossier_bruts.glob("*/*/*.json"))}
    assert all(set(a) == CHAMPS for a in articles.values())

    rss = articles["http://exemple.test/a1"]
    assert rss["publie_le"] == "2026-11-10T07:00:00+00:00"   # pubDate RFC 822 +0100 -> UTC
    assert rss["resume"] == "Resume un" and rss["rubrique"] == "Politique"

    atom = articles["http://exemple.test/atom1"]
    assert atom["publie_le"] == "2026-11-10T08:00:00+00:00"  # updated ISO 8601 +02:00 -> UTC
    assert atom["titre"] == "Article Atom é" and atom["resume"] == "Résumé atom"
    assert atom["rubrique"] == "Monde"
    assert "TEXTE INTEGRAL" not in json.dumps(atom)          # jamais le texte de l'article


def test_sync_sources_ne_perd_pas_l_etat(cfg, engine, serveur):
    ecrire_sources(cfg, ("A", serveur.url + "/a"), ("B", serveur.url + "/b"))
    assert db.synchroniser_sources(engine, cfg.fichier_sources)["ajoutees"] == 2
    db.noter_echec(engine, "A", "HTTP 503")

    ecrire_sources(cfg, ("A", serveur.url + "/a"))            # B retiree du CSV
    bilan = db.synchroniser_sources(engine, cfg.fichier_sources)
    etat = {s.nom: s for s in db.lister_sources(engine)}
    assert bilan["desactivees"] == 1 and not etat["B"].actif
    assert etat["A"].echecs_consecutifs == 1                  # compteur conserve
    assert db.migrer(engine, cfg.dossier_migrations) == []    # migrations idempotentes
