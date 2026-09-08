#!/usr/bin/env python3
"""Tests du cœur métier — **sans aucun appel réseau**.

On rejoue des réponses d'API réelles (relevées sur
`recherche-entreprises.api.gouv.fr`, y compris les cas tordus) et on vérifie le
verdict produit. Intérêt : ces tests passent hors ligne, en 0,1 s, et
verrouillent le comportement sur les cas limites qu'on a rencontrés.

Lancement (depuis le dossier SOUTENANCE/) :
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import date

# Permet de lancer les tests sans installer le paquet.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verificateur import analyse, entrees, sorties  # noqa: E402

# Date de référence figée : sans ça, un test sur « cessation récente »
# finirait par échouer tout seul avec le temps.
AUJOURDHUI = date(2026, 9, 8)

# --- Réponses d'API réelles, tronquées aux champs utilisés -------------------

ORANGE_ACTIVE = {
    "siren": "380129866",
    "nom_complet": "ORANGE",
    "etat_administratif": "A",
    "date_creation": "1990-12-31",
    "date_fermeture": None,
    "date_mise_a_jour": "2026-09-01T09:28:21",
    "tranche_effectif_salarie": "53",
    "activite_principale": "61.20Z",
    "nombre_etablissements_ouverts": 2200,
    "siege": {
        "siret": "38012986600377",
        "adresse": "111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX",
        "code_postal": "92130",
        "libelle_commune": "ISSY-LES-MOULINEAUX",
        "etat_administratif": "A",
        "date_fermeture": None,
    },
}

# Cas réel : cessation datée du 19/09/2025, soit 354 jours avant AUJOURDHUI.
CONSEIL_CESSEE_RECENTE = {
    "siren": "851643189",
    "nom_complet": "FREDERIC CONSEIL (TAXI SERVICES 22)",
    "etat_administratif": "C",
    "date_creation": "2019-06-01",
    "date_fermeture": "2025-09-19",
    "tranche_effectif_salarie": "NN",
    "nombre_etablissements_ouverts": 0,
    "siege": {
        "siret": "85164318900018",
        "adresse": "12 RUE DE LA GARE 22000 SAINT-BRIEUC",
        "etat_administratif": "F",
        "date_fermeture": "2025-09-19",
    },
}

# Cas réel piégeux : l'entreprise est marquée cessée (« C ») mais AUCUNE date de
# fermeture n'est renseignée, ni sur l'unité légale ni sur le siège.
BOULANGERIE_CESSEE_SANS_DATE = {
    "siren": "923804504",
    "nom_complet": "BOULANGERIE DE L'EUROPE (BOULANGERIE)",
    "etat_administratif": "C",
    "date_creation": "2023-07-20",
    "date_fermeture": None,
    "nombre_etablissements_ouverts": 0,
    "siege": {
        "siret": "92380450400010",
        "adresse": "395 RTE DEPARTEMENTALE 96 13710 FUVEAU",
        "etat_administratif": "F",
        "date_fermeture": None,
    },
}

# Cas réel : le SIREN existe mais `etat_administratif` vaut null.
JUND_ETAT_NULL = {
    "siren": "999999998",
    "nom_complet": "JACQUES JUND",
    "etat_administratif": None,
    "date_fermeture": None,
    "siege": {},
}


def prospect(nom="", identifiant="", ligne=2):
    """Raccourci pour fabriquer un Prospect dans les tests."""
    return entrees.Prospect(numero_ligne=ligne, nom=nom, identifiant_saisi=identifiant)


class TestValidationSiren(unittest.TestCase):
    """La clé de Luhn évite de dépenser un appel API sur une faute de frappe."""

    def test_siren_reels_valides(self):
        for siren in ("380129866", "503932568", "923804504", "851643189"):
            with self.subTest(siren=siren):
                self.assertTrue(entrees.cle_luhn_valide(siren))

    def test_siren_faute_de_frappe_rejete(self):
        self.assertFalse(entrees.cle_luhn_valide("123456789"))

    def test_prospect_siren_valide_est_exploitable(self):
        p = prospect(identifiant="380129866")
        self.assertEqual(p.type_identifiant, "siren")
        self.assertIsNone(p.motif_rejet)
        self.assertTrue(p.identifiant_exploitable)

    def test_prospect_siren_mauvaise_cle_rejete(self):
        p = prospect(identifiant="123456789")
        self.assertIn("clé de contrôle", p.motif_rejet)
        self.assertFalse(p.identifiant_exploitable)

    def test_prospect_trop_court_rejete(self):
        p = prospect(identifiant="12345678")
        self.assertIn("ni un SIREN", p.motif_rejet)

    def test_siren_avec_espaces_et_points_nettoye(self):
        # Copier-coller depuis un site : « 380 129 866 »
        p = prospect(identifiant="380 129 866")
        self.assertEqual(p.identifiant, "380129866")
        self.assertTrue(p.identifiant_exploitable)

    def test_siret_accepte(self):
        p = prospect(identifiant="38012986600377")
        self.assertEqual(p.type_identifiant, "siret")
        self.assertTrue(p.identifiant_exploitable)

    def test_sans_identifiant_pas_de_rejet(self):
        # Pas d'identifiant n'est pas une erreur : on cherchera par nom.
        p = prospect(nom="ORANGE")
        self.assertIsNone(p.motif_rejet)
        self.assertFalse(p.identifiant_exploitable)


class TestNormalisation(unittest.TestCase):
    """Comparer « SARL Café de l'Étoile » et « CAFE DE L ETOILE »."""

    def test_accents_et_ponctuation_supprimes(self):
        self.assertEqual(analyse.normaliser("Café de l'Étoile"), "CAFE DE L ETOILE")

    def test_forme_juridique_ignoree(self):
        self.assertEqual(
            analyse.normaliser("SARL Dupont"), analyse.normaliser("Dupont")
        )

    def test_nom_uniquement_juridique_non_vide(self):
        # « SARL » tout court ne doit pas se normaliser en chaîne vide.
        self.assertEqual(analyse.normaliser("SARL"), "SARL")

    def test_chaine_vide(self):
        self.assertEqual(analyse.normaliser(""), "")
        self.assertEqual(analyse.normaliser(None), "")

    def test_score_identique_est_100(self):
        self.assertEqual(analyse.score_ressemblance("Orange", "ORANGE"), 100)

    def test_score_nom_commercial_entre_parentheses(self):
        # L'API renvoie « RAISON SOCIALE (NOM COMMERCIAL) » : les deux comptent.
        score = analyse.score_ressemblance(
            "TAXI SERVICES 22", "FREDERIC CONSEIL (TAXI SERVICES 22)"
        )
        self.assertEqual(score, 100)

    def test_score_faible_pour_noms_etrangers(self):
        self.assertLess(
            analyse.score_ressemblance("Orange", "CARREFOUR"),
            analyse.SEUIL_CORRESPONDANCE,
        )


class TestAnalyseParIdentifiant(unittest.TestCase):
    def test_entreprise_active(self):
        fiche = analyse.analyser_par_identifiant(
            prospect(nom="ORANGE", identifiant="380129866"),
            [ORANGE_ACTIVE],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_VERIFIE)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_ACTIVE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_AUCUNE)
        self.assertEqual(fiche["siren"], "380129866")
        self.assertEqual(fiche["code_postal"], "92130")
        # Le code INSEE « 53 » doit être traduit, pas recopié brut.
        self.assertEqual(fiche["tranche_effectif"], "10 000 salariés et plus")
        self.assertIn("380129866", fiche["url_annuaire"])
        analyse.marquer_a_signaler(fiche)
        self.assertFalse(fiche["a_signaler"])

    def test_cessation_recente_signalee(self):
        fiche = analyse.analyser_par_identifiant(
            prospect(nom="FREDERIC CONSEIL", identifiant="851643189"),
            [CONSEIL_CESSEE_RECENTE],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_CESSEE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSATION_RECENTE)
        self.assertEqual(fiche["jours_depuis_cessation"], 354)
        self.assertTrue(fiche["cessation_recente"])
        analyse.marquer_a_signaler(fiche)
        self.assertTrue(fiche["a_signaler"])

    def test_meme_cessation_non_recente_avec_seuil_serre(self):
        # Le seuil est un paramètre métier : à 90 jours, la même cessation
        # n'est plus « récente » mais reste une cessation.
        fiche = analyse.analyser_par_identifiant(
            prospect(identifiant="851643189"),
            [CONSEIL_CESSEE_RECENTE],
            jours_recent=90,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)
        self.assertFalse(fiche["cessation_recente"])

    def test_cessee_sans_date_de_fermeture(self):
        """Cas limite réel : etat_administratif='C' mais date_fermeture=None."""
        fiche = analyse.analyser_par_identifiant(
            prospect(nom="BOULANGERIE DE L'EUROPE", identifiant="923804504"),
            [BOULANGERIE_CESSEE_SANS_DATE],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_CESSEE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)
        self.assertEqual(fiche["date_cessation"], "")
        self.assertEqual(fiche["jours_depuis_cessation"], "")
        self.assertIn("non renseignée", fiche["message"])

    def test_etat_administratif_null_devient_inconnu(self):
        """Un état absent ne doit jamais être interprété comme « active »."""
        fiche = analyse.analyser_par_identifiant(
            prospect(nom="JACQUES JUND", identifiant="999999998"),
            [JUND_ETAT_NULL],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_ETAT_INCONNU)
        analyse.marquer_a_signaler(fiche)
        self.assertTrue(fiche["a_signaler"])

    def test_siren_introuvable(self):
        fiche = analyse.analyser_par_identifiant(
            prospect(identifiant="000000000"),
            [],  # l'API a répondu 200 avec results vide
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_INTROUVABLE)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        analyse.marquer_a_signaler(fiche)
        self.assertTrue(fiche["a_signaler"])

    def test_nom_incoherent_avec_le_siren(self):
        """SIREN d'Orange + nom fantaisiste = ligne mal saisie, on prévient."""
        fiche = analyse.analyser_par_identifiant(
            prospect(nom="ORANGE MAIS FAUX NOM", identifiant="380129866"),
            [ORANGE_ACTIVE],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CORRESPONDANCE_INCERTAINE)
        self.assertIn("ne correspond pas", fiche["message"])

    def test_champs_manquants_ne_font_pas_planter(self):
        """L'API omet des champs selon les entreprises : aucun KeyError permis."""
        fiche = analyse.analyser_par_identifiant(
            prospect(identifiant="380129866"),
            [{"siren": "380129866"}],  # réponse volontairement squelettique
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["adresse_siege"], "")


class TestAnalyseParNom(unittest.TestCase):
    def test_correspondance_sure(self):
        fiche = analyse.analyser_par_nom(
            prospect(nom="Orange"),
            [ORANGE_ACTIVE],
            total=1,
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_VERIFIE)
        self.assertEqual(fiche["score_correspondance"], 100)

    def test_correspondance_douteuse_degradee(self):
        fiche = analyse.analyser_par_nom(
            prospect(nom="Entreprise Qui N Existe Pas"),
            [ORANGE_ACTIVE],  # l'API a renvoyé n'importe quoi de proche
            total=1,
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_INCERTAIN)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CORRESPONDANCE_INCERTAINE)

    def test_aucun_resultat(self):
        fiche = analyse.analyser_par_nom(
            prospect(nom="Zzzz Inexistant"),
            [],
            total=0,
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_INTROUVABLE)

    def test_meilleur_candidat_choisi_pas_le_premier(self):
        """On reclasse les candidats par ressemblance, pas par ordre API."""
        fiche = analyse.analyser_par_nom(
            prospect(nom="ORANGE"),
            [BOULANGERIE_CESSEE_SANS_DATE, ORANGE_ACTIVE],
            total=2,
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["siren"], "380129866")


class TestFichesDegradees(unittest.TestCase):
    def test_identifiant_invalide(self):
        p = prospect(nom="MAUVAISE SAISIE", identifiant="123456789")
        fiche = analyse.marquer_a_signaler(analyse.fiche_identifiant_invalide(p))
        self.assertEqual(
            fiche["statut_verification"], analyse.STATUT_IDENTIFIANT_INVALIDE
        )
        self.assertTrue(fiche["a_signaler"])

    def test_erreur_api_nest_pas_active(self):
        fiche = analyse.marquer_a_signaler(
            analyse.fiche_erreur_api(prospect(nom="ORANGE"), "délai dépassé")
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_ERREUR_API)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertTrue(fiche["a_signaler"])

    def test_toutes_les_fiches_ont_les_memes_colonnes(self):
        """Garantit un CSV de sortie à structure constante."""
        p = prospect(nom="X", identifiant="380129866")
        fiches = [
            analyse.fiche_vide(p),
            analyse.fiche_erreur_api(p, "test"),
            analyse.fiche_identifiant_invalide(prospect(identifiant="123456789")),
            analyse.analyser_par_identifiant(
                p, [ORANGE_ACTIVE], 365, AUJOURDHUI
            ),
        ]
        for fiche in fiches:
            with self.subTest(statut=fiche["statut_verification"]):
                self.assertEqual(set(fiche), set(sorties.COLONNES))


class TestLectureCsv(unittest.TestCase):
    """Le CSV du client est imparfait : le lecteur doit l'absorber."""

    def _ecrire(self, contenu, encodage="utf-8"):
        fichier = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding=encodage, newline=""
        )
        fichier.write(contenu)
        fichier.close()
        self.addCleanup(os.unlink, fichier.name)
        return fichier.name

    def test_separateur_point_virgule(self):
        chemin = self._ecrire("nom;siren\nORANGE;380129866\nCARREFOUR;503932568\n")
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(len(prospects), 2)
        self.assertEqual(prospects[0].identifiant, "380129866")

    def test_separateur_virgule(self):
        chemin = self._ecrire("nom,siren\nORANGE,380129866\n")
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(prospects[0].nom, "ORANGE")

    def test_alias_de_colonnes(self):
        chemin = self._ecrire("Raison_Sociale;Identifiant\nORANGE;380129866\n")
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(prospects[0].nom, "ORANGE")
        self.assertEqual(prospects[0].identifiant, "380129866")

    def test_lignes_vides_ignorees(self):
        chemin = self._ecrire("nom;siren\nORANGE;380129866\n;\n;\n")
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(len(prospects), 1)

    def test_encodage_cp1252(self):
        # Export Excel français : accents en cp1252, pas en UTF-8.
        chemin = self._ecrire("nom;siren\nSociété Générale;552120222\n", "cp1252")
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(prospects[0].nom, "Société Générale")

    def test_fichier_absent(self):
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects("/chemin/qui/nexiste/pas.csv")

    def test_fichier_vide(self):
        chemin = self._ecrire("")
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(chemin)

    def test_colonnes_inconnues(self):
        chemin = self._ecrire("colonne_a;colonne_b\n1;2\n")
        with self.assertRaises(entrees.FichierProspectsInvalide) as contexte:
            entrees.charger_prospects(chemin)
        self.assertIn("aucune colonne exploitable", str(contexte.exception))

    def test_aucune_ligne_exploitable(self):
        chemin = self._ecrire("nom;siren\n;\n;\n")
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(chemin)


class TestCompteurs(unittest.TestCase):
    def test_resume(self):
        # Un prospect sain, un prospect cessé, un prospect en erreur réseau.
        # Chaque nom saisi correspond bien à sa fiche, sinon on déclencherait
        # en plus une alerte de correspondance incertaine.
        fiches = [
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(
                    prospect(nom="ORANGE", identifiant="380129866"),
                    [ORANGE_ACTIVE],
                    365,
                    AUJOURDHUI,
                )
            ),
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(
                    prospect(nom="FREDERIC CONSEIL", identifiant="851643189"),
                    [CONSEIL_CESSEE_RECENTE],
                    365,
                    AUJOURDHUI,
                )
            ),
            analyse.marquer_a_signaler(
                analyse.fiche_erreur_api(prospect(nom="CARREFOUR"), "timeout")
            ),
        ]
        resume = analyse.compter(fiches)
        self.assertEqual(resume["total"], 3)
        self.assertEqual(resume["a_signaler"], 2)
        self.assertEqual(resume["par_etat"][analyse.ETAT_ACTIVE], 1)
        self.assertEqual(resume["par_etat"][analyse.ETAT_CESSEE], 1)
        self.assertEqual(
            resume["par_alerte"][analyse.ALERTE_CESSATION_RECENTE], 1
        )


class TestEcritureLivrables(unittest.TestCase):
    def test_trois_fichiers_ecrits(self):
        p = prospect(nom="ORANGE", identifiant="380129866")
        fiches = [
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(p, [ORANGE_ACTIVE], 365, AUJOURDHUI)
            ),
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(
                    p, [CONSEIL_CESSEE_RECENTE], 365, AUJOURDHUI
                )
            ),
        ]
        resume = analyse.compter(fiches)

        with tempfile.TemporaryDirectory() as dossier:
            chemins = sorties.ecrire_livrables(fiches, resume, dossier, {})
            for chemin in chemins.values():
                self.assertTrue(os.path.exists(chemin))

            with open(chemins["csv"], encoding="utf-8-sig") as fichier:
                entete = fichier.readline().strip()
            self.assertEqual(entete.split(";"), sorties.COLONNES)

            # alertes.csv ne contient que la ligne à signaler (+ l'en-tête).
            with open(chemins["alertes"], encoding="utf-8-sig") as fichier:
                lignes = fichier.read().strip().splitlines()
            self.assertEqual(len(lignes), 2)

    def test_booleens_lisibles_dans_le_csv(self):
        self.assertEqual(sorties._valeur_csv(True), "oui")
        self.assertEqual(sorties._valeur_csv(False), "non")
        self.assertEqual(sorties._valeur_csv(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
