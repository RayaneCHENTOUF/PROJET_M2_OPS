"""Contrôles de qualité : chaque fait passe sur des données saines et sonne quand il est faux."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from qualite import controles
from qualite.__main__ import main
from qualite.controles import controler

MAINTENANT = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
TRAITE_LE = "2026-11-12 09:45:00"


def propre(engine, id, source="Le Monde", titre="Un vrai titre", publie_le="2026-11-12 08:00:00",
           traite_le=TRAITE_LE):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO articles_propres (id, source, lien, titre, cle_titre, extrait, publie_le, recupere_le,"
            " rubrique, chemin_brut, passage, traite_le) VALUES (:id, :s, :l, :t, :t, '', :p, :p, NULL, 'x',"
            " 'p', :tl)"), {"id": id, "s": source, "l": f"https://ex.test/{id}", "t": titre, "p": publie_le,
                            "tl": traite_le})


def rejet(engine, id, source="Le Monde", traite_le=TRAITE_LE):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO rejets (id, source, titre, regle, detail, chemin_brut, passage, traite_le)"
            " VALUES (:id, :s, 't', 'R05_horoscope', '', 'x', 'p', :tl)"), {"id": id, "s": source, "tl": traite_le})


def par_nom(bilan):
    return {r.controle: r for r in bilan.resultats}


def test_donnees_saines_aucune_alerte(engine):
    for i in range(5):
        propre(engine, f"g{i}")
    rejet(engine, "r1")
    b = controler(engine, maintenant=MAINTENANT, webhook="")
    assert b.ok, b.echecs
    assert b.metriques[("taux_rejet", "Le Monde")] == pytest.approx(1 / 6)
    assert b.metriques[("articles_entres", "*")] == 6


def test_date_future_sonne(engine):
    propre(engine, "ok", publie_le="2026-11-12 10:30:00")      # dans la marge d'1 h : toléré
    propre(engine, "futur", publie_le="2026-11-14 08:00:00")
    r = par_nom(controler(engine, maintenant=MAINTENANT, webhook=""))["date_future"]
    assert not r.ok and r.valeur == 1 and "futur" in r.detail


def test_titre_vide_sonne(engine):
    propre(engine, "vide", titre="   ")
    r = par_nom(controler(engine, maintenant=MAINTENANT, webhook=""))["titre_vide"]
    assert not r.ok and r.valeur == 1


def test_taux_de_rejet_sonne_au_dessus_de_80_pourcent(engine):
    propre(engine, "g1", source="Le Dauphiné Libéré")
    for i in range(9):
        rejet(engine, f"r{i}", source="Le Dauphiné Libéré")
    r = par_nom(controler(engine, maintenant=MAINTENANT, webhook=""))["taux_rejet"]
    assert not r.ok and r.valeur == pytest.approx(0.9)
    assert "Le Dauphiné Libéré 90%" in r.detail


def test_taux_non_juge_sous_le_minimum_d_articles(engine):
    rejet(engine, "r1", source="Petite source")  # 1/1 = 100 %, mais pas significatif
    r = par_nom(controler(engine, maintenant=MAINTENANT, webhook=""))["taux_rejet"]
    assert r.ok and "non jugées" in r.detail


def test_taux_ignore_les_passages_hors_fenetre(engine):
    for i in range(6):
        rejet(engine, f"vieux{i}", traite_le="2026-11-10 09:00:00")
    for i in range(5):
        propre(engine, f"g{i}")
    assert controler(engine, maintenant=MAINTENANT, webhook="").ok


def test_metriques_publiees_en_base(engine):
    propre(engine, "g1")
    controler(engine, maintenant=MAINTENANT, webhook="")
    with engine.connect() as c:
        m = dict(c.execute(text(
            "SELECT nom, valeur FROM metriques_qualite WHERE source = '*'")).fetchall())
    assert m["controle_date_future"] == 1 and m["controle_taux_rejet"] == 1
    assert m["articles_gardes"] == 1 and m["dates_futures"] == 0


def test_webhook_appele_seulement_en_cas_d_echec(engine, monkeypatch):
    envois = []

    class Reponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("requests.post", lambda url, json, timeout: envois.append(json) or Reponse())
    propre(engine, "ok")
    controler(engine, maintenant=MAINTENANT, webhook="https://hook.test")
    assert envois == []

    propre(engine, "vide", titre="")
    controler(engine, maintenant=datetime(2026, 11, 12, 10, 1, tzinfo=timezone.utc), webhook="https://hook.test")
    assert len(envois) == 1 and "titre_vide" in envois[0]["text"]


def test_code_retour_de_la_commande(cfg, engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", cfg.database_url)
    monkeypatch.setenv("GAZETTE_ALERTE_WEBHOOK", "")
    propre(engine, "g1", traite_le=controles._sql(datetime.now(timezone.utc)))
    assert main(["controler"]) == 0
    propre(engine, "vide", titre="")
    assert main(["controler"]) == 1
