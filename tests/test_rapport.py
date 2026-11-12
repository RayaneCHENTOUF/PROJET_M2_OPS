"""Rapport du jour : un fichier par jour, réécrit à chaque passage, qui montre une règle trop gourmande."""

from datetime import date, datetime, timezone

from collect.stockage import StockageBrut
from qualite.controles import controler
from qualite.rapport import chemin_rapport, generer
from qualite.regles import REGLES
from qualite.traitement import traiter

from .test_traitement import MAINTENANT, brut, remplir


def test_rapport_du_jour(cfg, engine, tmp_path):
    remplir(cfg)
    traiter(cfg, engine, maintenant=MAINTENANT)
    controler(engine, maintenant=MAINTENANT, webhook="")
    chemin = generer(engine, tmp_path / "rapports", maintenant=MAINTENANT)

    assert chemin == tmp_path / "rapports" / "qualite-2026-11-12.md"
    r = chemin.read_text(encoding="utf-8")
    assert "# Rapport qualité — 2026-11-12" in r
    assert "| **Entrés** | 5 | 5 |" in r
    assert "| **Gardés** (`articles_propres`) | 2 | 2 |" in r
    assert "| **Rejetés** (`rejets`) | 3 (60,0 %) |" in r
    # par règle : toutes les règles listées, même celles qui n'ont rien rejeté
    for regle in REGLES:
        assert f"| `{regle.id}` |" in r
    # top 5 et exemples : on voit QUEL titre a été rejeté, et pourquoi
    assert "## Top 5 des rejets" in r
    assert "« Horoscope du jour : Bélier »" in r
    assert "titre identique déjà publié par Le Dauphiné Libéré" in r
    # contrôles du jour repris dans le rapport
    assert "| `taux_rejet` | ✅ vrai |" in r


def test_un_fichier_par_jour_reecrit_a_chaque_passage(cfg, engine, tmp_path):
    dossier = tmp_path / "rapports"
    s = StockageBrut(cfg.dossier_bruts)
    s.ecrire(brut("Le Monde", "Budget 2027 : l'Assemblée adopte la première partie", n=1))
    traiter(cfg, engine, maintenant=MAINTENANT)
    generer(engine, dossier, maintenant=MAINTENANT)

    plus_tard = datetime(2026, 11, 12, 10, 30, tzinfo=timezone.utc)
    s.ecrire(brut("Le Monde", "Grève SNCF : le trafic très perturbé ce jeudi", n=2))
    traiter(cfg, engine, maintenant=plus_tard)
    generer(engine, dossier, maintenant=plus_tard)

    assert [p.name for p in dossier.iterdir()] == ["qualite-2026-11-12.md"]
    r = (dossier / "qualite-2026-11-12.md").read_text(encoding="utf-8")
    assert "2 passage(s)" in r
    assert "| **Entrés** | 2 | 1 |" in r  # journée cumulée | dernier passage seul


def test_regle_gourmande_signalee(cfg, engine, tmp_path):
    s = StockageBrut(cfg.dossier_bruts)
    for i in range(6):
        s.ecrire(brut("Le Figaro", f"Article magazine numéro {i}", rubrique="Magazine Lifestyle", n=i))
    traiter(cfg, engine, maintenant=MAINTENANT)
    r = generer(engine, tmp_path, maintenant=MAINTENANT).read_text(encoding="utf-8")
    assert "**Le Figaro** perd 100,0 % de ses articles (6/6), surtout par `R08_magazine` (6)" in r


def test_jour_sans_article(engine, tmp_path):
    r = generer(engine, tmp_path, maintenant=MAINTENANT, jour=date(2026, 1, 1)).read_text(encoding="utf-8")
    assert "Aucun article traité ce jour-là." in r
    assert chemin_rapport(tmp_path, date(2026, 1, 1)).exists()
