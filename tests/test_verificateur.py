#!/usr/bin/env python3
"""Tests du cœur métier, sans aucun appel réseau.

On rejoue des réponses relevées sur l'API réelle, y compris ses cas tordus, et
on vérifie le verdict produit.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

# Lancer les tests sans installer le paquet.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verificateur import analyse, api, entrees, sorties  # noqa: E402

# Figée : sinon un test sur « cessation récente » finirait par échouer seul.
AUJOURDHUI = date(2026, 9, 8)

# Réponses d'API réelles, réduites aux champs utilisés.

ORANGE_ACTIVE = {
    "siren": "380129866",
    "nom_complet": "ORANGE",
    "etat_administratif": "A",
    "date_fermeture": None,
    "siege": {
        "siret": "38012986646013",
        "adresse": "111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX",
    },
}

CESSEE_RECENTE = {
    "siren": "851643189",
    "nom_complet": "FREDERIC CONSEIL (TAXI SERVICES 22)",
    "etat_administratif": "C",
    "date_fermeture": "2025-09-19",
    "siege": {"siret": "85164318900026", "adresse": "26 RUE DOCTEUR ROUX 22000 SAINT-BRIEUC"},
}

CESSEE_SANS_DATE = {          # cas réel : marquée « C » sans date de fermeture
    "siren": "923804504",
    "nom_complet": "BOULANGERIE DE L'EUROPE (BOULANGERIE)",
    "etat_administratif": "C",
    "date_fermeture": None,
    "siege": {"siret": "92380450400010", "date_fermeture": None},
}

ETAT_NULL = {                 # cas réel : SIREN existant, sans état administratif
    "siren": "999999998",
    "nom_complet": "JACQUES JUND",
    "etat_administratif": None,
    "siege": {},
}


def prospect(nom="", identifiant="", rang=1):
    """Raccourci de construction d'un prospect pour les tests."""
    return entrees.Prospect(rang=rang, nom=nom, identifiant_saisi=identifiant)


class TestValidationIdentifiant(unittest.TestCase):
    def test_siren_reel_valide(self):
        self.assertTrue(entrees.cle_luhn_valide("380129866"))     # ORANGE

    def test_faute_de_frappe_rejetee(self):
        self.assertFalse(entrees.cle_luhn_valide("123456789"))

    def test_siren_valide_interrogeable(self):
        self.assertEqual(prospect(identifiant="380129866").siren, "380129866")

    def test_siret_reduit_a_son_siren(self):
        """L'API ne sait chercher que par SIREN : un SIRET doit être tronqué."""
        self.assertEqual(prospect(identifiant="38012986646013").siren, "380129866")

    def test_espaces_et_points_nettoyes(self):
        self.assertEqual(prospect(identifiant="380.129.866").siren, "380129866")

    def test_mauvaise_cle_rejetee(self):
        rejet = prospect(identifiant="123456789")
        self.assertEqual(rejet.siren, "")
        self.assertIn("clé de contrôle", rejet.motif_rejet)

    def test_trop_court_rejete(self):
        self.assertIn("ni un SIREN", prospect(identifiant="12345678").motif_rejet)

    def test_sans_identifiant_pas_de_rejet(self):
        sans = prospect(nom="Dupont Consulting")
        self.assertIsNone(sans.motif_rejet)
        self.assertEqual(sans.siren, "")


class TestCorrespondanceDeNom(unittest.TestCase):
    def test_accents_et_ponctuation_supprimes(self):
        self.assertEqual(
            analyse.mots_significatifs("Café de l'Étoile"),
            {"CAFE", "DE", "L", "ETOILE"},
        )

    def test_forme_juridique_ignoree(self):
        self.assertEqual(
            analyse.mots_significatifs("SARL Dupont"),
            analyse.mots_significatifs("Dupont"),
        )

    def test_nom_uniquement_juridique_reste_non_vide(self):
        self.assertEqual(analyse.mots_significatifs("SARL"), {"SARL"})

    def test_score_identique_est_100(self):
        self.assertEqual(analyse.score_ressemblance("ORANGE", "ORANGE"), 100)

    def test_score_ignore_le_sigle_entre_parentheses(self):
        """L'API renvoie « RAISON SOCIALE (SIGLE) » : le sigle en trop ne compte pas."""
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

    def test_score_partiel(self):
        """Deux mots saisis sur quatre retrouvés : 50 %."""
        self.assertEqual(
            analyse.score_ressemblance("ALPHA BETA GAMMA DELTA", "ALPHA BETA"), 50
        )


class TestAnalyseParSiren(unittest.TestCase):
    def test_entreprise_active(self):
        fiche = analyse.analyser(
            prospect("ORANGE", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_ACTIVE)
        self.assertEqual(fiche["alerte"], "")          # rien à signaler
        self.assertIn("ISSY", fiche["adresse_siege"])
        self.assertEqual(fiche["siret_siege"], "38012986646013")
        self.assertTrue(fiche["url_annuaire"].endswith("380129866"))

    def test_cessation_recente_signalee(self):
        fiche = analyse.analyser(
            prospect("FREDERIC CONSEIL", "851643189"), [CESSEE_RECENTE], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_CESSEE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSATION_RECENTE)
        self.assertEqual(fiche["date_cessation"], "2025-09-19")

    def test_cessation_ancienne_signalee_sans_urgence(self):
        vieille = dict(CESSEE_RECENTE, date_fermeture="2020-01-31")
        fiche = analyse.analyser(prospect("X", "851643189"), [vieille], AUJOURDHUI)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)

    def test_cessee_sans_date_de_fermeture(self):
        fiche = analyse.analyser(
            prospect("BOULANGERIE DE L'EUROPE", "923804504"),
            [CESSEE_SANS_DATE],
            AUJOURDHUI,
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)
        self.assertIn("date non renseignée", fiche["message"])

    def test_etat_null_devient_inconnu_jamais_active(self):
        fiche = analyse.analyser(
            prospect("JACQUES JUND", "999999998"), [ETAT_NULL], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_ETAT_INCONNU)

    def test_introuvable(self):
        fiche = analyse.analyser(prospect("", "000000000"), [], AUJOURDHUI)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_INTROUVABLE)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)

    def test_nom_incoherent_avec_le_siren(self):
        """Un SIREN juste avec le nom d'une autre société : ligne mal saisie."""
        fiche = analyse.analyser(
            prospect("ORANGE MAIS FAUX NOM", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CORRESPONDANCE_INCERTAINE)
        self.assertIn("Correspondance douteuse", fiche["message"])

    def test_siren_seul_sans_nom_ne_declenche_pas_de_doute(self):
        fiche = analyse.analyser(prospect("", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI)
        self.assertEqual(fiche["score_correspondance"], 100)
        self.assertEqual(fiche["alerte"], "")

    def test_champs_manquants_ne_font_pas_planter(self):
        # Entreprise réduite à son SIREN : aucun KeyError ne doit sortir.
        fiche = analyse.analyser(
            prospect("", "380129866"), [{"siren": "380129866"}], AUJOURDHUI
        )
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["adresse_siege"], "")


class TestAnalyseParNom(unittest.TestCase):
    def test_correspondance_sure(self):
        fiche = analyse.analyser(prospect("Orange"), [ORANGE_ACTIVE], AUJOURDHUI)
        self.assertEqual(fiche["score_correspondance"], 100)
        self.assertEqual(fiche["alerte"], "")

    def test_correspondance_douteuse_signalee(self):
        fiche = analyse.analyser(
            prospect("Dupont Consulting"), [ORANGE_ACTIVE], AUJOURDHUI
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CORRESPONDANCE_INCERTAINE)

    def test_meilleur_candidat_choisi_pas_le_premier(self):
        fiche = analyse.analyser(
            prospect("FREDERIC CONSEIL"),
            [ORANGE_ACTIVE, CESSEE_RECENTE],       # le bon est en second
            AUJOURDHUI,
        )
        self.assertEqual(fiche["siren"], "851643189")

    def test_aucun_resultat(self):
        fiche = analyse.analyser(prospect("Entreprise Inexistante"), [])
        self.assertEqual(fiche["alerte"], analyse.ALERTE_INTROUVABLE)


class TestFichesDegradees(unittest.TestCase):
    def test_erreur_api_nest_pas_active(self):
        """Une API en panne ne doit jamais se traduire par « entreprise active »."""
        fiche = analyse.fiche_erreur_api(prospect("ORANGE"), "délai dépassé")
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_INCONNU)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_ERREUR_API)

    def test_toutes_les_fiches_ont_les_memes_champs(self):
        """Le rapport livré doit être régulier, même quand tout se passe mal."""
        fiches = [
            analyse.analyser(prospect("X", "380129866"), [ORANGE_ACTIVE]),
            analyse.analyser(prospect("Y"), []),
            analyse.fiche_identifiant_invalide(prospect("Z", "123456789")),
            analyse.fiche_erreur_api(prospect("W"), "réseau"),
        ]
        champs = set(fiches[0])
        for fiche in fiches:
            self.assertEqual(set(fiche), champs)

    def test_compteurs(self):
        fiches = [
            analyse.analyser(prospect("ORANGE", "380129866"), [ORANGE_ACTIVE], AUJOURDHUI),
            analyse.analyser(prospect("X", "851643189"), [CESSEE_RECENTE], AUJOURDHUI),
            analyse.fiche_erreur_api(prospect("Y"), "timeout"),
        ]
        resume = analyse.compter(fiches)
        self.assertEqual(resume["total"], 3)
        self.assertEqual(resume["a_signaler"], 2)
        self.assertEqual(resume["par_etat"][analyse.ETAT_ACTIVE], 1)
        self.assertEqual(resume["par_alerte"][analyse.ALERTE_CESSATION_RECENTE], 1)


class TestLectureJson(unittest.TestCase):
    def _fichier(self, contenu, encodage="utf-8"):
        """Écrit un fichier temporaire et renvoie son chemin."""
        descripteur, chemin = tempfile.mkstemp(suffix=".json")
        os.close(descripteur)
        with open(chemin, "w", encoding=encodage) as fichier:
            fichier.write(contenu)
        self.addCleanup(os.unlink, chemin)
        return chemin

    def test_tableau_simple(self):
        chemin = self._fichier('[{"nom": "ORANGE", "siren": "380129866"}]')
        prospects = entrees.charger_prospects(chemin)
        self.assertEqual(len(prospects), 1)
        self.assertEqual(prospects[0].nom, "ORANGE")
        self.assertEqual(prospects[0].rang, 1)

    def test_cle_siret_acceptee(self):
        chemin = self._fichier('[{"nom": "ORANGE", "siret": "38012986646013"}]')
        self.assertEqual(entrees.charger_prospects(chemin)[0].siren, "380129866")

    def test_accents_conserves(self):
        chemin = self._fichier('[{"nom": "Société Générale"}]')
        self.assertEqual(entrees.charger_prospects(chemin)[0].nom, "Société Générale")

    def test_identifiant_numerique_tolere(self):
        """Un SIREN écrit sans guillemets ne doit pas faire planter la lecture."""
        chemin = self._fichier('[{"nom": "ORANGE", "siren": 380129866}]')
        self.assertEqual(entrees.charger_prospects(chemin)[0].siren, "380129866")

    def test_entrees_vides_ignorees(self):
        chemin = self._fichier('[{"nom": "ORANGE"}, {}, "pas un objet"]')
        self.assertEqual(len(entrees.charger_prospects(chemin)), 1)

    def test_fichier_absent(self):
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects("/introuvable/prospects.json")

    def test_json_mal_forme_indique_la_position(self):
        """Une virgule en trop doit produire un message actionnable."""
        chemin = self._fichier('[{"nom": "ORANGE"},]')
        with self.assertRaises(entrees.FichierProspectsInvalide) as contexte:
            entrees.charger_prospects(chemin)
        self.assertIn("ligne", str(contexte.exception))

    def test_json_valide_mais_pas_un_tableau(self):
        chemin = self._fichier('{"nom": "ORANGE"}')
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(chemin)

    def test_tableau_sans_prospect_exploitable(self):
        with self.assertRaises(entrees.FichierProspectsInvalide):
            entrees.charger_prospects(self._fichier("[{}, {}]"))


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
        # Attente neutralisée : le test doit rester instantané.
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
        self.assertEqual(api.chercher("380129866"), [ORANGE_ACTIVE])

    def test_429_puis_succes(self):
        """Un dépassement de quota est passager : on réessaie."""
        self._simuler([
            self.ReponseFactice(429),
            self.ReponseFactice(200, {"results": []}),
        ])
        self.assertEqual(api.chercher("380129866"), [])
        self.assertEqual(len(self.appels), 2)

    def test_400_ne_declenche_pas_de_nouvelle_tentative(self):
        """Une erreur définitive doit échouer tout de suite."""
        self._simuler([self.ReponseFactice(400)])
        with self.assertRaises(api.ErreurAPI):
            api.chercher("(((")
        self.assertEqual(len(self.appels), 1)

    def test_timeout_epuise_les_tentatives(self):
        self._simuler([api.requests.Timeout()] * api.TENTATIVES_MAX)
        with self.assertRaises(api.ErreurAPI) as contexte:
            api.chercher("380129866")
        self.assertIn("délai dépassé", str(contexte.exception))

    def test_200_mais_pas_du_json(self):
        self._simuler([self.ReponseFactice(200)])
        with self.assertRaises(api.ErreurAPI):
            api.chercher("380129866")


class TestRapportJson(unittest.TestCase):
    def test_rapport_ecrit_et_relisible(self):
        fiches = [
            analyse.analyser(
                prospect("ORANGE", "380129866", rang=1), [ORANGE_ACTIVE], AUJOURDHUI
            ),
            analyse.analyser(
                prospect("FREDERIC CONSEIL", "851643189", rang=2),
                [CESSEE_RECENTE],
                AUJOURDHUI,
            ),
        ]
        resume = analyse.compter(fiches)

        with tempfile.TemporaryDirectory() as dossier:
            chemin = sorties.ecrire_rapport(
                fiches, resume, dossier, parametres={"fichier_entree": "test.json"}
            )
            with open(chemin, encoding="utf-8") as fichier:
                rapport = json.load(fichier)

        self.assertEqual(rapport["meta"]["resume"]["a_signaler"], 1)
        self.assertIn("genere_le", rapport["meta"])
        self.assertEqual(rapport["meta"]["parametres"]["fichier_entree"], "test.json")

        # Les prospects à ne pas démarcher sont ceux qui portent une alerte.
        signales = [p for p in rapport["prospects"] if p["alerte"]]
        self.assertEqual(len(signales), 1)
        self.assertEqual(signales[0]["alerte"], analyse.ALERTE_CESSATION_RECENTE)
        self.assertIn("FREDERIC CONSEIL", signales[0]["nom_officiel"])


if __name__ == "__main__":
    unittest.main()
