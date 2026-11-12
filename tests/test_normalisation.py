"""Un test (ou plus) par fonction de normalisation. Cas tirés de pollueurs réels ou attendus (docs/qualite.md)."""

from qualite.normalisation import (
    borner_extrait,
    cle_titre,
    date_utc,
    decoder_entites,
    nettoyer_texte,
    normaliser,
    normaliser_espaces,
    reparer_encodage,
    retirer_html,
)


# --- reparer_encodage -------------------------------------------------------

def test_reparer_encodage_mojibake_courant():
    assert reparer_encodage("Ã©lection prÃ©sidentielle Ã\xa0 Paris") == "élection présidentielle à Paris"
    assert reparer_encodage("lâ€™Ã‰lysÃ©e") == "l’Élysée"


def test_reparer_encodage_a_grave_dont_l_insecable_est_perdue():
    assert reparer_encodage("Ã\xa0 Paris") == "à Paris"  # mojibake intact
    # l'insécable est déjà devenue une espace : on répare « à » sans deviner quelle espace supprimer,
    # normaliser_espaces réduira ensuite le doublon
    assert reparer_encodage("Ã  Paris") == "à  Paris"
    assert nettoyer_texte("Ã  Paris") == "à Paris"


def test_reparer_encodage_texte_mixte():
    # un caractère hors cp1252 (emoji) ne doit pas bloquer la réparation du reste
    assert reparer_encodage("ðŸ”´ DIRECT. Ã‰lection") == "ðŸ”´ DIRECT. Élection"
    assert reparer_encodage("🔴 Ã©tÃ©") == "🔴 été"


def test_reparer_encodage_double():
    assert reparer_encodage("ÃƒÂ©tÃƒÂ©") == "été"


def test_reparer_encodage_texte_sain_inchange():
    sain = "« Été » : l’Élysée, 2 500 €, ½ — Ça va ?"
    assert reparer_encodage(sain) == sain


# --- decoder_entites --------------------------------------------------------

def test_decoder_entites():
    assert decoder_entites("Tom &amp; Jerry &eacute;t&#233; &quot;ok&quot;") == 'Tom & Jerry été "ok"'


def test_decoder_entites_double_echappement():
    assert decoder_entites("caf&amp;eacute;") == "café"


# --- retirer_html -----------------------------------------------------------

def test_retirer_html():
    assert retirer_html('<p>Un <a href="x">lien</a><br/>suite</p>').split() == ["Un", "lien", "suite"]


def test_retirer_html_script_et_style():
    assert "alert" not in retirer_html("<script>alert(1)</script>Texte<style>p{}</style>")


def test_retirer_html_garde_les_comparaisons():
    # « < » isolé n'est pas une balise : on ne doit pas manger le texte
    assert retirer_html("3 < 5 et 7 > 2") == "3 < 5 et 7 > 2"


# --- normaliser_espaces -----------------------------------------------------

def test_normaliser_espaces():
    assert normaliser_espaces("  Un titre\n\t avec « espaces »  ") == "Un titre avec « espaces »"


def test_normaliser_espaces_invisibles_et_nfc():
    # « e » + accent combinant -> « é » précomposé ; zero-width et BOM retirés
    assert normaliser_espaces("﻿ét​é") == "été"


# --- nettoyer_texte (enchaînement) -------------------------------------------

def test_nettoyer_texte_enchaine_tout():
    brut = " &lt;p&gt;Ã‰lection&nbsp;: le <b>rÃ©sultat</b>&lt;/p&gt; "
    assert nettoyer_texte(brut) == "Élection : le résultat"


def test_nettoyer_texte_vide():
    assert nettoyer_texte(None) == ""
    assert nettoyer_texte("  <br/> ") == ""


# --- borner_extrait ---------------------------------------------------------

def test_borner_extrait_court_inchange():
    assert borner_extrait("Court.", 300) == "Court."


def test_borner_extrait_coupe_sur_un_mot():
    texte = "un deux trois quatre cinq"
    assert borner_extrait(texte, 12) == "un deux…"          # « trois » aurait été coupé
    assert len(borner_extrait("x" * 500, 300)) == 300       # un seul mot géant : coupe franche


def test_borner_extrait_respecte_la_taille():
    texte = "mot " * 200
    assert len(borner_extrait(texte, 300)) <= 300


# --- date_utc ---------------------------------------------------------------

def test_date_utc_convertit_le_fuseau():
    assert date_utc("2026-11-12T10:00:00+01:00") == "2026-11-12T09:00:00+00:00"
    assert date_utc("2026-11-12T08:00:00Z") == "2026-11-12T08:00:00+00:00"


def test_date_utc_sans_fuseau_suppose_utc():
    assert date_utc("2026-11-12T08:00:00") == "2026-11-12T08:00:00+00:00"


def test_date_utc_absente_ou_illisible():
    assert date_utc(None) is None
    assert date_utc("mardi dernier") is None


# --- cle_titre --------------------------------------------------------------

def test_cle_titre_retire_prefixes_accents_ponctuation():
    assert cle_titre("VIDÉOS. New York : un immeuble s’effondre") == "new york un immeuble s effondre"
    assert cle_titre("Isère. Info EBRA. Lait, agrumes") == "lait agrumes"


def test_cle_titre_ne_retire_pas_monsieur():
    assert cle_titre("M. Macron s'exprime") == "m macron s exprime"


def test_cle_titre_ne_retire_pas_une_phrase_entiere():
    # pas de « . » suivi d'une espace dans les 30 premiers caractères : rien n'est retiré
    assert cle_titre("Le gouvernement annonce un plan de relance majeur. Les syndicats réagissent").startswith("le gouvernement")


# --- normaliser (article complet) --------------------------------------------

def test_normaliser_article():
    brut = {
        "id": "abc", "source": "Le Monde", "lien": "https://ex.test/a",
        "titre": " Ã‰lection : <i>rÃ©sultats</i> ", "resume": "<p>" + "mot " * 200 + "</p>",
        "publie_le": "2026-11-12T10:00:00+01:00", "recupere_le": "2026-11-12T09:30:00+00:00",
        "rubrique": "Politique", "payload": {"title": "…"},
    }
    a = normaliser(brut)
    assert a["titre"] == "Élection : résultats"
    assert a["cle_titre"] == "election resultats"
    assert len(a["extrait"]) <= 300 and a["extrait"].endswith("…")
    assert a["publie_le"] == "2026-11-12T09:00:00+00:00"
    assert "payload" not in a and "resume" not in a        # on ne recopie que le republiable
    assert brut["titre"] == " Ã‰lection : <i>rÃ©sultats</i> "  # le brut n'est pas modifié
