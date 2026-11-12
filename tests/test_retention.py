"""Rétention et contrat de données : durées appliquées, traçabilité conservée, rien de personnel stocké."""

from datetime import datetime, timezone

from sqlalchemy import text

from collect.items import vers_article
from collect.stockage import StockageBrut
from qualite import retention
from qualite.traitement import traiter

from .test_traitement import MAINTENANT, brut

PLUS_TARD = datetime(2026, 12, 20, 3, 0, tzinfo=timezone.utc)  # 38 jours après le traitement


def _compter(engine, table):
    with engine.connect() as c:
        return c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def preparer(cfg, engine):
    s = StockageBrut(cfg.dossier_bruts)
    s.ecrire(brut("Le Monde", "Budget 2027 : l'Assemblée adopte la première partie", n=1))
    s.ecrire(brut("Le Monde", "Horoscope du jour : Bélier", n=2))
    traiter(cfg, engine, maintenant=MAINTENANT)


def test_simulation_ne_touche_a_rien(cfg, engine):
    preparer(cfg, engine)
    b = retention.appliquer(cfg, engine, maintenant=PLUS_TARD, simulation=True)
    assert b.partitions_archivees == ["2026-11-12"] and b.rejets_supprimes == 1
    assert (cfg.dossier_bruts / "2026-11-12").exists()
    assert _compter(engine, "rejets") == 1


def test_bruts_archives_rejets_purges_propres_gardes(cfg, engine):
    preparer(cfg, engine)
    b = retention.appliquer(cfg, engine, maintenant=PLUS_TARD)

    assert b.partitions_archivees == ["2026-11-12"]
    assert not (cfg.dossier_bruts / "2026-11-12").exists()
    assert (retention.dossier_archives(cfg) / "2026-11-12.tar.gz").exists()
    assert _compter(engine, "rejets") == 0              # 30 j
    assert _compter(engine, "articles_propres") == 1    # 1 an : toujours là


def test_tracabilite_apres_archivage(cfg, engine):
    preparer(cfg, engine)
    retention.appliquer(cfg, engine, maintenant=PLUS_TARD)
    with engine.connect() as c:
        id_, source, chemin = c.execute(text("SELECT id, source, chemin_brut FROM articles_propres")).one()
    original = retention.retrouver_brut(chemin, cfg)  # lu dans l'archive du jour
    assert original["id"] == id_ and original["source"] == source


def test_un_an_plus_tard_tout_est_purge(cfg, engine):
    preparer(cfg, engine)
    retention.appliquer(cfg, engine, maintenant=PLUS_TARD)
    b = retention.appliquer(cfg, engine, maintenant=datetime(2027, 11, 20, tzinfo=timezone.utc))
    assert b.archives_supprimees == ["2026-11-12.tar.gz"] and b.propres_supprimes == 1
    assert _compter(engine, "articles_propres") == 0


def test_archivage_repris_apres_interruption(cfg, engine, tmp_path):
    """Partition déjà archivée en partie (passage interrompu) : l'archive est complétée, rien n'est perdu."""
    s = StockageBrut(cfg.dossier_bruts)
    s.ecrire(brut("Le Monde", "Premier", n=1))
    retention.appliquer(cfg, engine, maintenant=PLUS_TARD)
    s.ecrire(brut("Le Monde", "Second", n=2))  # même jour de récupération, réapparu après l'archivage
    retention.appliquer(cfg, engine, maintenant=PLUS_TARD)

    import tarfile
    with tarfile.open(retention.dossier_archives(cfg) / "2026-11-12.tar.gz") as tar:
        assert len(tar.getnames()) == 2


def test_payload_sans_donnees_personnelles_ni_texte_integral():
    entree = {
        "title": "Un titre", "link": "https://ex.test/a", "summary": "x" * 5000,
        "summary_detail": {"value": "x" * 5000}, "content": [{"value": "TEXTE INTÉGRAL"}],
        "author": "Jeanne Dupont", "authors": [{"name": "Jeanne Dupont"}], "author_detail": {"name": "Jeanne Dupont"},
        "credit": "PHOTOGRAPHE/AGENCE", "media_credit": [{"content": "PHOTOGRAPHE"}],
        "media_content": [{"url": "https://img.test/a.jpg"}], "tags": [{"term": "Politique"}],
    }
    a = vers_article(entree, "Le Monde", "2026-11-12T09:30:00+00:00")
    assert len(a["resume"]) == 1000
    assert "Jeanne" not in str(a) and "PHOTOGRAPHE" not in str(a) and "img.test" not in str(a)
    assert "TEXTE INTÉGRAL" not in str(a) and "x" * 1001 not in str(a)
    assert a["payload"]["tags"] == [{"term": "Politique"}]
