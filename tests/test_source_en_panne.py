"""Test « source en panne » : un serveur HTTP local qui renvoie 503."""

from collect import db
from collect.collecteur import collecter_tout

from .conftest import RSS, ecrire_sources


def test_503_reessaie_avec_backoff_puis_compte_un_echec(cfg, engine, serveur):
    serveur.routes["/panne"] = (503, b"indisponible", {})
    ecrire_sources(cfg, ("En panne", serveur.url + "/panne"))
    db.synchroniser_sources(engine, cfg.fichier_sources)

    attentes = []
    [bilan] = collecter_tout(cfg, engine, dormir=attentes.append)

    assert bilan.statut == "echec" and bilan.http == 503
    assert serveur.appels("/panne") == cfg.tentatives == 3      # on a bien reessaye
    assert len(attentes) == 2 and attentes[0] < attentes[1]     # attente croissante
    assert db.lister_sources(engine)[0].echecs_consecutifs == 1


def test_trois_echecs_de_suite_endorment_la_source_sans_bloquer_les_autres(cfg, engine, serveur):
    serveur.routes["/panne"] = (503, b"", {})
    serveur.routes["/ok"] = (200, RSS, {"Content-Type": "application/rss+xml"})
    ecrire_sources(cfg, ("En panne", serveur.url + "/panne"), ("Saine", serveur.url + "/ok"))
    db.synchroniser_sources(engine, cfg.fichier_sources)

    for _ in range(3):
        bilans = {b.source: b for b in collecter_tout(cfg, engine, dormir=lambda s: None)}
        assert bilans["Saine"].statut in ("ok", "inchange")
    assert bilans["En panne"].echecs == 3

    # 4e passage : la source dort, on ne la sollicite plus
    appels_avant = serveur.appels("/panne")
    bilans = {b.source: b for b in collecter_tout(cfg, engine, dormir=lambda s: None)}
    assert bilans["En panne"].statut == "sommeil"
    assert serveur.appels("/panne") == appels_avant

    # le reveil manuel la remet dans la boucle, et une reussite remet le compteur a zero
    serveur.routes["/panne"] = (200, RSS, {})
    assert db.reveiller(engine, "En panne")
    bilans = {b.source: b for b in collecter_tout(cfg, engine, dormir=lambda s: None)}
    assert bilans["En panne"].statut == "ok"
    etat = {s.nom: s for s in db.lister_sources(engine)}["En panne"]
    assert etat.echecs_consecutifs == 0 and etat.derniere_reussite is not None


def test_404_est_permanente_pas_de_nouvel_essai(cfg, engine, serveur):
    ecrire_sources(cfg, ("Disparue", serveur.url + "/n-existe-pas"))
    db.synchroniser_sources(engine, cfg.fichier_sources)

    [bilan] = collecter_tout(cfg, engine, dormir=lambda s: None)

    assert bilan.statut == "echec" and bilan.http == 404
    assert serveur.appels("/n-existe-pas") == 1


def test_flux_casse_compte_comme_echec(cfg, engine, serveur):
    serveur.routes["/html"] = (200, b"<html><body>pas un flux</body></html>", {"Content-Type": "text/html"})
    ecrire_sources(cfg, ("Cassee", serveur.url + "/html"))
    db.synchroniser_sources(engine, cfg.fichier_sources)

    [bilan] = collecter_tout(cfg, engine, dormir=lambda s: None)

    assert bilan.statut == "echec"
    assert db.lister_sources(engine)[0].echecs_consecutifs == 1


def test_serveur_injoignable_est_transitoire(cfg, engine):
    ecrire_sources(cfg, ("Eteinte", "http://127.0.0.1:9/flux"))  # port 9 : personne n'ecoute
    db.synchroniser_sources(engine, cfg.fichier_sources)

    [bilan] = collecter_tout(cfg, engine, dormir=lambda s: None)

    assert bilan.statut == "echec" and bilan.essais == 3
