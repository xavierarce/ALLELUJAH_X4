#!/usr/bin/env python3
"""Tests du cœur métier — **sans aucun appel réseau**.

On rejoue des réponses de l'API (relevées sur
`recherche-entreprises.api.gouv.fr`, y compris les cas tordus) et on vérifie le
verdict produit. Intérêt : ces tests passent hors ligne, en une fraction de
seconde, et verrouillent le comportement sur les cas limites rencontrés.

Lancement, depuis la racine du projet :
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import date

# Permet de lancer les tests sans installer le paquet.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verificateur import analyse, api, entrees, sorties  # noqa: E402

# Date de référence figée : sans ça, un test sur « cessation récente »
# finirait par échouer tout seul avec le temps.
AUJOURDHUI = date(2026, 9, 8)

# --- Réponses d'API réelles, réduites aux champs utilisés --------------------

ORANGE_ACTIVE = {
    "siren": "380129866",
    "nom_complet": "ORANGE",
    "etat_administratif": "A",
    "date_creation": "1990-12-31",
    "date_fermeture": None,
    "siege": {
        "siret": "38012986646013",
        "adresse": "111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX",
    },
}

CESSEE_RECENTE = {
    "siren": "923804504",
    "nom_complet": "BOULANGERIE DE L'EUROPE",
    "etat_administratif": "C",
    "date_fermeture": "2026-03-15",
    "siege": {"siret": "92380450400015", "adresse": "12 RUE DE L'EUROPE 75008 PARIS"},
}

CESSEE_ANCIENNE = {
    "siren": "851643189",
    "nom_complet": "FREDERIC CONSEIL",
    "etat_administratif": "C",
    "date_fermeture": "2020-01-31",
    "siege": {},
}

ETAT_NULL = {          # cas réel : le SIREN existe, mais sans état administratif
    "siren": "999999998",
    "nom_complet": "JACQUES JUND",
    "etat_administratif": None,
    "siege": {},
}


def prospect(nom="", identifiant="", ligne=2):
    """Raccourci de construction d'un prospect pour les tests."""
    return entrees.Prospect(numero_ligne=ligne, nom=nom, identifiant_saisi=identifiant)


class TestValidationIdentifiant(unittest.TestCase):
    """Séance 5 : la regex et la clé de Luhn filtrent avant tout appel API."""

    def test_siren_reel_valide(self):
        self.assertTrue(entrees.cle_luhn_valide("380129866"))     # ORANGE

    def test_faute_de_frappe_rejetee(self):
        self.assertFalse(entrees.cle_luhn_valide("123456789"))

    def test_siren_valide_est_exploitable(self):
        self.assertTrue(prospect(identifiant="380129866").identifiant_exploitable)

    def test_mauvaise_cle_rejetee(self):
        rejet = prospect(identifiant="123456789")
        self.assertFalse(rejet.identifiant_exploitable)
        self.assertIn("clé de contrôle", rejet.motif_rejet)

    def test_trop_court_rejete(self):
        self.assertIn("ni un SIREN", prospect(identifiant="12345678").motif_rejet)

    def test_espaces_et_points_nettoyes(self):
        self.assertEqual(prospect(identifiant="380 129 866").identifiant, "380129866")

    def test_siret_accepte(self):
        self.assertTrue(prospect(identifiant="38012986646013").identifiant_exploitable)

    def test_sans_identifiant_pas_de_rejet(self):
        sans = prospect(nom="Dupont Consulting")
        self.assertIsNone(sans.motif_rejet)
        self.assertFalse(sans.identifiant_exploitable)


class TestCorrespondanceDeNom(unittest.TestCase):
    def test_accents_et_ponctuation_supprimes(self):
        self.assertEqual(analyse.normaliser("Café de l'Étoile"), "CAFE DE L ETOILE")

    def test_forme_juridique_ignoree(self):
        self.assertEqual(
            analyse.normaliser("SARL Dupont"), analyse.normaliser("Dupont")
        )

    def test_nom_uniquement_juridique_reste_non_vide(self):
        self.assertEqual(analyse.normaliser("SARL"), "SARL")

    def test_score_identique_est_100(self):
        self.assertEqual(analyse.score_ressemblance("ORANGE", "ORANGE"), 100)

    def test_score_ignore_le_sigle_entre_parentheses(self):
        """L'API renvoie « RAISON SOCIALE (SIGLE) » : ce n'est pas un écart."""
        self.assertEqual(
            analyse.score_ressemblance(
                "FREDERIC CONSEIL", "FREDERIC CONSEIL (TAXI SERVICES 22)"
            ),
            100,
        )

    def test_score_faible_pour_noms_differents(self):
        self.assertLess(
            analyse.score_ressemblance("Dupont Consulting", "CARREFOUR"),
            analyse.SEUIL_CORRESPONDANCE,
        )


class TestAnalyseParIdentifiant(unittest.TestCase):
    def test_entreprise_active(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("ORANGE", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_VERIFIE)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_ACTIVE)
        self.assertFalse(analyse.marquer_a_signaler(fiche)["a_signaler"])
        self.assertIn("ISSY", fiche["adresse_siege"])
        self.assertTrue(fiche["url_annuaire"].endswith("380129866"))

    def test_cessation_recente_signalee(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("BOULANGERIE DE L'EUROPE", "923804504"),
            [CESSEE_RECENTE],
            AUJOURDHUI,
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_CESSEE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSATION_RECENTE)
        self.assertTrue(analyse.marquer_a_signaler(fiche)["a_signaler"])

    def test_cessation_ancienne_signalee_sans_urgence(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("FREDERIC CONSEIL", "851643189"), [CESSEE_ANCIENNE], AUJOURDHUI
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)

    def test_etat_null_devient_inconnu_jamais_active(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("JACQUES JUND", "999999998"), [ETAT_NULL], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_ETAT_INCONNU)

    def test_identifiant_introuvable(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("", "000000000"), [], AUJOURDHUI
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_INTROUVABLE)

    def test_nom_incoherent_avec_le_siren(self):
        fiche = analyse.analyser_par_identifiant(
            prospect("ORANGE MAIS FAUX NOM", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CORRESPONDANCE_INCERTAINE)
        self.assertIn("ne correspond pas", fiche["message"])

    def test_champs_manquants_ne_font_pas_planter(self):
        # Une entreprise réduite à son SIREN : aucun KeyError ne doit sortir.
        fiche = analyse.analyser_par_identifiant(
            prospect("X", "380129866"), [{"siren": "380129866"}], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["adresse_siege"], "")


class TestAnalyseParNom(unittest.TestCase):
    def test_correspondance_sure(self):
        fiche = analyse.analyser_par_nom(prospect("Orange"), [ORANGE_ACTIVE], AUJOURDHUI)
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_VERIFIE)
        self.assertEqual(fiche["score_correspondance"], 100)

    def test_correspondance_douteuse_degradee(self):
        fiche = analyse.analyser_par_nom(
            prospect("Dupont Consulting"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_INCERTAIN)
        self.assertTrue(analyse.marquer_a_signaler(fiche)["a_signaler"])

    def test_meilleur_candidat_choisi_pas_le_premier(self):
        fiche = analyse.analyser_par_nom(
            prospect("FREDERIC CONSEIL"),
            [ORANGE_ACTIVE, CESSEE_ANCIENNE],       # le bon est en second
            AUJOURDHUI,
        )
        self.assertEqual(fiche["siren"], "851643189")

    def test_aucun_resultat(self):
        fiche = analyse.analyser_par_nom(prospect("Entreprise Inexistante"), [])
        self.assertEqual(fiche["alerte"], analyse.ALERTE_INTROUVABLE)


class TestFichesDegradees(unittest.TestCase):
    def test_erreur_api_nest_pas_active(self):
        """Une API en panne ne doit jamais se traduire par « entreprise active »."""
        fiche = analyse.fiche_erreur_api(prospect("ORANGE"), "délai dépassé")
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertTrue(analyse.marquer_a_signaler(fiche)["a_signaler"])

    def test_toutes_les_fiches_ont_les_memes_colonnes(self):
        """Le CSV livré doit être régulier, même quand tout se passe mal."""
        fiches = [
            analyse.analyser_par_identifiant(prospect("X", "380129866"), [ORANGE_ACTIVE]),
            analyse.analyser_par_nom(prospect("Y"), []),
            analyse.fiche_identifiant_invalide(prospect("Z", "123456789")),
            analyse.fiche_erreur_api(prospect("W"), "réseau"),
        ]
        for fiche in fiches:
            self.assertEqual(set(fiche), set(sorties.COLONNES))


class TestLectureCsv(unittest.TestCase):
    def _fichier(self, contenu, encodage="utf-8"):
        """Écrit un CSV temporaire et renvoie son chemin."""
        descripteur, chemin = tempfile.mkstemp(suffix=".csv")
        os.close(descripteur)
        with open(chemin, "w", encoding=encodage, newline="") as fichier:
            fichier.write(contenu)
        self.addCleanup(os.unlink, chemin)
        return chemin

    def test_separateur_point_virgule(self):
        chemin = self._fichier("nom;siren\nORANGE;380129866\n")
        self.assertEqual(len(entrees.charger_prospects(chemin)), 1)

    def test_separateur_virgule(self):
        chemin = self._fichier("nom,siren\nORANGE,380129866\nCARREFOUR,503932568\n")
        self.assertEqual(len(entrees.charger_prospects(chemin)), 2)

    def test_alias_de_colonnes(self):
        chemin = self._fichier("raison_sociale;siret\nORANGE;38012986646013\n")
        self.assertEqual(entrees.charger_prospects(chemin)[0].nom, "ORANGE")

    def test_lignes_vides_ignorees(self):
        chemin = self._fichier("nom;siren\nORANGE;380129866\n;\n")
        self.assertEqual(len(entrees.charger_prospects(chemin)), 1)

    def test_encodage_excel_francais(self):
        chemin = self._fichier("nom;siren\nSociété Générale;\n", encodage="cp1252")
        self.assertEqual(entrees.charger_prospects(chemin)[0].nom, "Société Générale")

    def test_fichier_absent(self):
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects("/introuvable/prospects.csv")

    def test_fichier_vide(self):
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(self._fichier("\n"))

    def test_colonnes_inconnues(self):
        chemin = self._fichier("ville;code_postal\nParis;75008\n")
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(chemin)


class TestAppelApi(unittest.TestCase):
    """Robustesse du client HTTP, vérifiée en remplaçant `requests`."""

    class ReponseFactice:
        def __init__(self, code, corps=None):
            self.status_code = code
            self._corps = corps

        def json(self):
            if self._corps is None:
                raise ValueError("pas du JSON")
            return self._corps

    def setUp(self):
        self.appels = []
        # On neutralise l'attente entre deux tentatives : le test doit rester
        # instantané.
        self._attente = api.ATTENTE
        api.ATTENTE = 0

    def tearDown(self):
        api.ATTENTE = self._attente

    def _simuler(self, reponses):
        """Remplace `api.requests.get` par une séquence de réponses/exceptions."""
        file_attente = list(reponses)

        def faux_get(*args, **kwargs):
            self.appels.append(kwargs.get("params"))
            resultat = file_attente.pop(0)
            if isinstance(resultat, Exception):
                raise resultat
            return resultat

        original = api.requests.get
        api.requests.get = faux_get
        self.addCleanup(lambda: setattr(api.requests, "get", original))

    def test_reponse_200_exploitee(self):
        self._simuler([self.ReponseFactice(200, {"results": [ORANGE_ACTIVE]})])
        self.assertEqual(api.chercher_par_identifiant("380129866"), [ORANGE_ACTIVE])

    def test_429_puis_succes(self):
        """Un dépassement de quota est passager : on réessaie."""
        self._simuler([
            self.ReponseFactice(429),
            self.ReponseFactice(200, {"results": []}),
        ])
        self.assertEqual(api.chercher_par_identifiant("380129866"), [])
        self.assertEqual(len(self.appels), 2)

    def test_400_ne_declenche_pas_de_nouvelle_tentative(self):
        """Une erreur définitive doit échouer tout de suite."""
        self._simuler([self.ReponseFactice(400)])
        with self.assertRaises(api.ErreurAPI):
            api.chercher_par_nom("(((")
        self.assertEqual(len(self.appels), 1)

    def test_timeout_epuise_les_tentatives(self):
        self._simuler([api.requests.Timeout()] * api.TENTATIVES_MAX)
        with self.assertRaises(api.ErreurAPI) as contexte:
            api.chercher_par_identifiant("380129866")
        self.assertIn("délai dépassé", str(contexte.exception))


class TestLivrables(unittest.TestCase):
    def test_trois_fichiers_ecrits_et_alertes_filtrees(self):
        fiches = [
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(
                    prospect("ORANGE", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI
                )
            ),
            analyse.marquer_a_signaler(
                analyse.analyser_par_identifiant(
                    prospect("BOULANGERIE DE L'EUROPE", "923804504"),
                    [CESSEE_RECENTE],
                    AUJOURDHUI,
                )
            ),
        ]
        resume = analyse.compter(fiches)
        self.assertEqual(resume["a_signaler"], 1)

        with tempfile.TemporaryDirectory() as dossier:
            chemins = sorties.ecrire_livrables(
                fiches, resume, dossier, parametres={"fichier_entree": "test.csv"}
            )
            for chemin in chemins.values():
                self.assertTrue(os.path.exists(chemin))

            with open(chemins["alertes"], encoding="utf-8-sig") as fichier:
                lignes = fichier.read().strip().splitlines()
            # En-tête + la seule entreprise cessée.
            self.assertEqual(len(lignes), 2)
            self.assertIn("BOULANGERIE", lignes[1])
            # Booléens rendus lisibles pour un tableur.
            self.assertIn(";oui;", lignes[1])


if __name__ == "__main__":
    unittest.main()
