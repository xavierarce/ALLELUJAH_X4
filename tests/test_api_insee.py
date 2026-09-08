#!/usr/bin/env python3
"""Tests de l'adaptateur INSEE — **sans appel réseau ni clé API**.

On rejoue des réponses réelles de l'API Sirene 3.11 (relevées avec une clé, puis
tronquées aux champs utilisés) et on vérifie que l'adaptateur les traduit bien
dans le format commun attendu par `analyse.py`.

L'enjeu est là : si la traduction est juste, tout le reste du programme
(analyse, alertes, livrables) fonctionne à l'identique sur les deux sources.

Lancement (depuis le dossier du projet) :
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import threading
import time
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verificateur import analyse, api_insee, entrees  # noqa: E402

AUJOURDHUI = date(2026, 9, 8)

# --- Réponses réelles de l'API Sirene, tronquées -----------------------------

# GET /siret?q=siren:380129866 AND etablissementSiege:true
ORANGE_SIRET = {
    "siren": "380129866",
    "siret": "38012986648625",
    "nic": "48625",
    "etablissementSiege": True,
    "dateCreationEtablissement": "2020-06-01",
    "adresseEtablissement": {
        "numeroVoieEtablissement": "111",
        "indiceRepetitionEtablissement": None,
        "typeVoieEtablissement": "QUAI",
        "libelleVoieEtablissement": "DU PRESIDENT ROOSEVELT",
        "codePostalEtablissement": "92130",
        "libelleCommuneEtablissement": "ISSY-LES-MOULINEAUX",
        "libelleCommuneEtrangerEtablissement": None,
    },
    "periodesEtablissement": [
        {
            "dateFin": None,
            "dateDebut": "2020-06-01",
            "etatAdministratifEtablissement": "A",
            "changementEtatAdministratifEtablissement": False,
        }
    ],
    "uniteLegale": {
        "denominationUniteLegale": "ORANGE",
        "sigleUniteLegale": None,
        "etatAdministratifUniteLegale": "A",
        "dateCreationUniteLegale": "1990-12-31",
        "activitePrincipaleUniteLegale": "61.10Z",
        "trancheEffectifsUniteLegale": "53",
        "categorieEntreprise": "GE",
        "dateDernierTraitementUniteLegale": "2026-09-01T09:36:11.281",
    },
}

# Entreprise cessée avec un vrai changement d'état daté.
CONSEIL_SIRET = {
    "siren": "851643189",
    "siret": "85164318900026",
    "etablissementSiege": True,
    "dateCreationEtablissement": "2019-07-01",
    "adresseEtablissement": {
        "numeroVoieEtablissement": "26",
        "typeVoieEtablissement": "RUE",
        "libelleVoieEtablissement": "DOCTEUR ROUX",
        "codePostalEtablissement": "22000",
        "libelleCommuneEtablissement": "SAINT-BRIEUC",
    },
    "periodesEtablissement": [
        {
            "dateFin": None,
            "dateDebut": "2025-09-19",
            "etatAdministratifEtablissement": "F",
            "changementEtatAdministratifEtablissement": True,
        }
    ],
    "uniteLegale": {
        "denominationUniteLegale": "FREDERIC CONSEIL",
        "sigleUniteLegale": "TAXI SERVICES 22",
        "etatAdministratifUniteLegale": "C",
        "dateCreationUniteLegale": "2019-07-01",
        "trancheEffectifsUniteLegale": "NN",
        "dateDernierTraitementUniteLegale": "2025-09-20T04:00:00.000",
    },
}

# GET /siren/851643189 — trois périodes, la première marque la cessation.
CONSEIL_UNITE_LEGALE = {
    "uniteLegale": {
        "siren": "851643189",
        "nombrePeriodesUniteLegale": 3,
        "dateCreationUniteLegale": "2019-07-01",
        "periodesUniteLegale": [
            {
                "dateDebut": "2025-09-19",
                "dateFin": None,
                "etatAdministratifUniteLegale": "C",
                "changementEtatAdministratifUniteLegale": True,
            },
            {
                "dateDebut": "2019-10-30",
                "dateFin": "2025-09-18",
                "etatAdministratifUniteLegale": "A",
                "changementEtatAdministratifUniteLegale": False,
            },
        ],
    }
}

# Piège : cessée, mais UNE SEULE période dont `dateDebut` est la date de
# CRÉATION. La prendre pour une date de cessation serait un contresens.
BOULANGERIE_UNITE_LEGALE = {
    "uniteLegale": {
        "siren": "923804504",
        "nombrePeriodesUniteLegale": 1,
        "dateCreationUniteLegale": "2023-07-20",
        "periodesUniteLegale": [
            {
                "dateDebut": "2023-07-20",
                "dateFin": None,
                "etatAdministratifUniteLegale": "C",
                "changementEtatAdministratifUniteLegale": False,
            }
        ],
    }
}

BOULANGERIE_SIRET = {
    "siren": "923804504",
    "siret": "92380450400010",
    "etablissementSiege": True,
    "adresseEtablissement": {
        "numeroVoieEtablissement": "395",
        "typeVoieEtablissement": "RTE",
        "libelleVoieEtablissement": "DEPARTEMENTALE 96",
        "codePostalEtablissement": "13710",
        "libelleCommuneEtablissement": "FUVEAU",
    },
    "periodesEtablissement": [
        {
            "dateFin": None,
            "dateDebut": "2023-07-20",
            "etatAdministratifEtablissement": "F",
            "changementEtatAdministratifEtablissement": False,
        }
    ],
    "uniteLegale": {
        "denominationUniteLegale": "BOULANGERIE DE L'EUROPE",
        "sigleUniteLegale": "BOULANGERIE",
        "etatAdministratifUniteLegale": "C",
        "dateCreationUniteLegale": "2023-07-20",
    },
}

# Entrepreneur individuel : pas de dénomination, un nom et un prénom.
PERSONNE_PHYSIQUE_SIRET = {
    "siren": "123456782",
    "siret": "12345678200011",
    "etablissementSiege": True,
    "adresseEtablissement": {"codePostalEtablissement": "75001"},
    "periodesEtablissement": [{"etatAdministratifEtablissement": "A"}],
    "uniteLegale": {
        "denominationUniteLegale": None,
        "prenomUsuelUniteLegale": "MARIE",
        "nomUniteLegale": "DURAND",
        "etatAdministratifUniteLegale": "A",
    },
}


class FausseReponse:
    """Remplace `_appeler` : renvoie une réponse figée selon le chemin demandé."""

    def __init__(self, reponses):
        self.reponses = reponses
        self.appels = []

    def __call__(self, chemin, parametres=None):
        self.appels.append((chemin, parametres))
        for prefixe, charge in self.reponses.items():
            if chemin.startswith(prefixe):
                return charge
        return {}


class TestConfigurationCle(unittest.TestCase):
    """La clé vient de l'environnement, jamais du code."""

    def setUp(self):
        self.valeur_initiale = os.environ.get(api_insee.VARIABLE_CLE)

    def tearDown(self):
        if self.valeur_initiale is None:
            os.environ.pop(api_insee.VARIABLE_CLE, None)
        else:
            os.environ[api_insee.VARIABLE_CLE] = self.valeur_initiale

    def test_cle_absente_leve_une_erreur_explicite(self):
        os.environ.pop(api_insee.VARIABLE_CLE, None)
        with self.assertRaises(api_insee.CleInseeManquante) as contexte:
            api_insee.cle_api()
        # Le message doit dire à l'utilisateur quoi faire.
        self.assertIn(api_insee.VARIABLE_CLE, str(contexte.exception))

    def test_cle_vide_traitee_comme_absente(self):
        os.environ[api_insee.VARIABLE_CLE] = "   "
        with self.assertRaises(api_insee.CleInseeManquante):
            api_insee.cle_api()

    def test_cle_presente(self):
        os.environ[api_insee.VARIABLE_CLE] = "cle-de-test"
        self.assertEqual(api_insee.cle_api(), "cle-de-test")

    def test_entete_attendu_par_insee(self):
        # Vérifié en réel : Authorization Bearer et ?apikey= renvoient 401.
        self.assertEqual(api_insee.ENTETE_CLE, "X-INSEE-Api-Key-Integration")


class TestConstructionAdresse(unittest.TestCase):
    """L'INSEE éclate l'adresse en 15 champs, on la recompose sur une ligne."""

    def test_adresse_complete(self):
        self.assertEqual(
            api_insee._construire_adresse(ORANGE_SIRET["adresseEtablissement"]),
            "111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX",
        )

    def test_champs_manquants_pas_de_double_espace(self):
        adresse = api_insee._construire_adresse(
            {"codePostalEtablissement": "75001", "libelleCommuneEtablissement": "PARIS"}
        )
        self.assertEqual(adresse, "75001 PARIS")

    def test_adresse_vide(self):
        self.assertEqual(api_insee._construire_adresse({}), "")
        self.assertEqual(api_insee._construire_adresse(None), "")

    def test_commune_etrangere_utilisee_en_repli(self):
        adresse = api_insee._construire_adresse(
            {
                "libelleCommuneEtablissement": None,
                "libelleCommuneEtrangerEtablissement": "GENEVE",
            }
        )
        self.assertEqual(adresse, "GENEVE")


class TestDenomination(unittest.TestCase):
    def test_societe_avec_sigle(self):
        self.assertEqual(
            api_insee._denomination(CONSEIL_SIRET["uniteLegale"]),
            "FREDERIC CONSEIL (TAXI SERVICES 22)",
        )

    def test_societe_sans_sigle(self):
        self.assertEqual(
            api_insee._denomination(ORANGE_SIRET["uniteLegale"]), "ORANGE"
        )

    def test_personne_physique_recomposee(self):
        self.assertEqual(
            api_insee._denomination(PERSONNE_PHYSIQUE_SIRET["uniteLegale"]),
            "MARIE DURAND",
        )

    def test_unite_legale_vide(self):
        self.assertEqual(api_insee._denomination({}), "")


class TestAssainissementRecherche(unittest.TestCase):
    """Les caractères réservés font répondre « erreur de syntaxe » à l'INSEE."""

    def test_guillemets_retires(self):
        self.assertNotIn('"', api_insee._assainir_nom('SOCIETE "GENERALE"'))

    def test_caracteres_lucene_retires(self):
        for caractere in "+-!(){}[]^~*?:/\\":
            with self.subTest(caractere=caractere):
                self.assertNotIn(
                    caractere, api_insee._assainir_nom(f"NOM{caractere}TEST")
                )

    def test_nom_normal_preserve(self):
        self.assertEqual(api_insee._assainir_nom("SOCIETE GENERALE"), "SOCIETE GENERALE")

    def test_nom_vide(self):
        self.assertEqual(api_insee._assainir_nom(""), "")
        self.assertEqual(api_insee._assainir_nom(None), "")


class TestTraductionFormatCommun(unittest.TestCase):
    """Le point clé : une fiche INSEE doit être indiscernable d'une fiche
    de l'API ouverte pour tout le reste du programme."""

    def test_entreprise_active(self):
        commun = api_insee._convertir(ORANGE_SIRET)
        self.assertEqual(commun["siren"], "380129866")
        self.assertEqual(commun["nom_complet"], "ORANGE")
        self.assertEqual(commun["etat_administratif"], "A")
        self.assertIsNone(commun["date_fermeture"])
        self.assertEqual(commun["siege"]["code_postal"], "92130")
        self.assertEqual(commun["siege"]["etat_administratif"], "A")
        # Les codes de tranche d'effectif sont les mêmes dans les deux sources.
        self.assertEqual(commun["tranche_effectif_salarie"], "53")

    def test_les_deux_sources_donnent_la_meme_fiche(self):
        """Traduction INSEE + analyse = même verdict que l'API ouverte."""
        commun = api_insee._convertir(ORANGE_SIRET)
        fiche = analyse.analyser_par_identifiant(
            entrees.Prospect(2, "ORANGE", "380129866"),
            [commun],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["statut_verification"], analyse.STATUT_VERIFIE)
        self.assertEqual(fiche["etat_activite"], analyse.ETAT_ACTIVE)
        self.assertEqual(fiche["alerte"], analyse.ALERTE_AUCUNE)
        self.assertEqual(fiche["tranche_effectif"], "10 000 salariés et plus")
        self.assertEqual(
            fiche["adresse_siege"],
            "111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX",
        )

    def test_cessation_datee_recuperee(self):
        original = api_insee._appeler
        api_insee._appeler = FausseReponse({"/siren/": CONSEIL_UNITE_LEGALE})
        try:
            commun = api_insee._convertir(CONSEIL_SIRET)
        finally:
            api_insee._appeler = original

        self.assertEqual(commun["etat_administratif"], "C")
        self.assertEqual(commun["date_fermeture"], "2025-09-19")
        self.assertEqual(commun["siege"]["date_fermeture"], "2025-09-19")

        fiche = analyse.analyser_par_identifiant(
            entrees.Prospect(2, "FREDERIC CONSEIL", "851643189"),
            [commun],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSATION_RECENTE)
        self.assertEqual(fiche["jours_depuis_cessation"], 354)

    def test_cessee_sans_changement_date_non_inventee(self):
        """Le piège : `dateDebut` de la seule période est la date de CRÉATION.

        On doit renvoyer « date inconnue » plutôt que d'affirmer une cessation
        le jour de la création de l'entreprise.
        """
        original = api_insee._appeler
        api_insee._appeler = FausseReponse({"/siren/": BOULANGERIE_UNITE_LEGALE})
        try:
            commun = api_insee._convertir(BOULANGERIE_SIRET)
        finally:
            api_insee._appeler = original

        self.assertEqual(commun["etat_administratif"], "C")
        self.assertIsNone(commun["date_fermeture"])
        self.assertIsNone(commun["siege"]["date_fermeture"])
        # Surtout : la date de création n'a PAS été prise pour une cessation.
        self.assertNotEqual(commun["date_fermeture"], "2023-07-20")

        fiche = analyse.analyser_par_identifiant(
            entrees.Prospect(2, "BOULANGERIE DE L'EUROPE", "923804504"),
            [commun],
            jours_recent=365,
            aujourdhui=AUJOURDHUI,
        )
        self.assertEqual(fiche["alerte"], analyse.ALERTE_CESSEE)
        self.assertIn("non renseignée", fiche["message"])

    def test_pas_d_appel_supplementaire_si_entreprise_active(self):
        """L'appel de la date de cessation coûte du quota : il doit être évité."""
        original = api_insee._appeler
        faux = FausseReponse({})
        api_insee._appeler = faux
        try:
            api_insee._convertir(ORANGE_SIRET)
        finally:
            api_insee._appeler = original
        self.assertEqual(faux.appels, [])

    def test_etablissement_squelettique(self):
        """Aucun KeyError même sur une réponse minimale."""
        commun = api_insee._convertir({"siren": "380129866", "siret": "x"})
        self.assertEqual(commun["siren"], "380129866")
        self.assertEqual(commun["siege"]["adresse"], "")
        self.assertIsNone(commun["etat_administratif"])


class TestRecherches(unittest.TestCase):
    def setUp(self):
        self.original = api_insee._appeler

    def tearDown(self):
        api_insee._appeler = self.original

    def test_recherche_par_siren(self):
        faux = FausseReponse({"/siret": {"etablissements": [ORANGE_SIRET]}})
        api_insee._appeler = faux
        resultats = api_insee.chercher_par_identifiant("380129866")

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0]["nom_complet"], "ORANGE")
        chemin, parametres = faux.appels[0]
        self.assertEqual(chemin, "/siret")
        self.assertIn("siren:380129866", parametres["q"])
        self.assertIn("etablissementSiege:true", parametres["q"])

    def test_siret_ramene_au_siren(self):
        """Un SIRET saisi doit ramener le SIÈGE, comme l'API ouverte."""
        faux = FausseReponse({"/siret": {"etablissements": [ORANGE_SIRET]}})
        api_insee._appeler = faux
        api_insee.chercher_par_identifiant("38012986600377")
        self.assertIn("siren:380129866", faux.appels[0][1]["q"])

    def test_siren_absent_de_sirene(self):
        api_insee._appeler = FausseReponse({"/siret": {"etablissements": []}})
        self.assertEqual(api_insee.chercher_par_identifiant("000000000"), [])

    def test_reponse_404_traduite_en_liste_vide(self):
        # `_appeler` renvoie {} sur un 404 : pas une panne, une absence.
        api_insee._appeler = FausseReponse({})
        self.assertEqual(api_insee.chercher_par_identifiant("000000000"), [])

    def test_recherche_par_nom(self):
        faux = FausseReponse(
            {"/siret": {"etablissements": [ORANGE_SIRET], "header": {"total": 42}}}
        )
        api_insee._appeler = faux
        candidats, total = api_insee.chercher_par_nom("ORANGE")

        self.assertEqual(total, 42)
        self.assertEqual(candidats[0]["nom_complet"], "ORANGE")
        self.assertIn('denominationUniteLegale:"ORANGE"', faux.appels[0][1]["q"])

    def test_recherche_par_nom_vide_n_appelle_pas_l_api(self):
        faux = FausseReponse({})
        api_insee._appeler = faux
        self.assertEqual(api_insee.chercher_par_nom("  "), ([], 0))
        self.assertEqual(faux.appels, [])

    def test_meme_interface_que_l_autre_client(self):
        """Les deux modules doivent être interchangeables sans adaptation."""
        from verificateur import api_entreprises

        for fonction in ("chercher_par_identifiant", "chercher_par_nom"):
            with self.subTest(fonction=fonction):
                self.assertTrue(hasattr(api_insee, fonction))
                self.assertTrue(hasattr(api_entreprises, fonction))
        # Et le même type d'exception, pour que l'orchestrateur n'en attrape qu'un.
        self.assertIs(api_insee.ErreurAPI, api_entreprises.ErreurAPI)


class TestLimiteur(unittest.TestCase):
    """30 requêtes/minute : sans limiteur, un pool de threads part en 429."""

    def test_laisse_passer_sous_le_quota(self):
        limiteur = api_insee.Limiteur(requetes=5, fenetre=60)
        debut = time.monotonic()
        for _ in range(5):
            limiteur.attendre_son_tour()
        # Aucune attente ne doit avoir eu lieu.
        self.assertLess(time.monotonic() - debut, 0.5)

    def test_freine_au_dela_du_quota(self):
        limiteur = api_insee.Limiteur(requetes=2, fenetre=0.4)
        debut = time.monotonic()
        for _ in range(4):        # 2 passent, puis on attend la fenêtre
            limiteur.attendre_son_tour()
        self.assertGreaterEqual(time.monotonic() - debut, 0.4)

    def test_thread_safe(self):
        """Le compteur ne doit pas se corrompre entre threads."""
        limiteur = api_insee.Limiteur(requetes=50, fenetre=60)
        erreurs = []

        def travail():
            try:
                for _ in range(5):
                    limiteur.attendre_son_tour()
            except Exception as erreur:      # pragma: no cover
                erreurs.append(erreur)

        threads = [threading.Thread(target=travail) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(erreurs, [])
        self.assertEqual(len(limiteur._appels), 50)

    def test_workers_recommandes_coherents_avec_le_quota(self):
        # 30 req/min : inutile de lancer 5 threads, ils attendraient.
        self.assertLessEqual(api_insee.WORKERS_RECOMMANDES, 3)
        self.assertLess(api_insee.REQUETES_PAR_FENETRE, 30)

    def test_pause_imposee_bloque_les_appels(self):
        limiteur = api_insee.Limiteur(requetes=100, fenetre=60)
        limiteur.imposer_pause(0.3)
        debut = time.monotonic()
        limiteur.attendre_son_tour()
        self.assertGreaterEqual(time.monotonic() - debut, 0.3)

    def test_pause_negative_ignoree(self):
        limiteur = api_insee.Limiteur(requetes=100, fenetre=60)
        limiteur.imposer_pause(-5)
        debut = time.monotonic()
        limiteur.attendre_son_tour()
        self.assertLess(time.monotonic() - debut, 0.2)


class TestAlignementSurLeQuotaServeur(unittest.TestCase):
    """Le compteur local ignore les autres exécutions ; les en-têtes non.

    C'est le bug observé en test réel : deux lancements à une minute
    d'intervalle repartaient d'un compteur vide et prenaient une rafale de 429.
    """

    def test_conversion_epoch_millisecondes(self):
        maintenant = 1_788_876_300.0                   # epoch secondes
        reset = str(int((maintenant + 42) * 1000))      # epoch millisecondes
        attente = api_insee.secondes_jusqu_au_reset(reset, maintenant=maintenant)
        self.assertAlmostEqual(attente, 42.0, places=1)

    def test_reset_deja_passe(self):
        maintenant = 1_788_876_300.0
        reset = str(int((maintenant - 10) * 1000))
        self.assertEqual(
            api_insee.secondes_jusqu_au_reset(reset, maintenant=maintenant), 0.0
        )

    def test_entete_absent_replie_sur_la_fenetre(self):
        self.assertEqual(
            api_insee.secondes_jusqu_au_reset(None), api_insee.FENETRE_SECONDES
        )

    def test_entete_illisible_replie_sur_la_fenetre(self):
        self.assertEqual(
            api_insee.secondes_jusqu_au_reset("bientot"), api_insee.FENETRE_SECONDES
        )

    def test_attente_bornee(self):
        maintenant = 1_788_876_300.0
        # Un en-tête absurde (dans 10 ans) ne doit pas figer l'outil.
        reset = str(int((maintenant + 315_360_000) * 1000))
        self.assertEqual(
            api_insee.secondes_jusqu_au_reset(reset, maintenant=maintenant),
            api_insee.ATTENTE_MAX,
        )

    def test_quota_epuise_declenche_une_pause(self):
        limiteur = api_insee.Limiteur(requetes=100, fenetre=60)
        reset = str(int((time.time() + 0.4) * 1000))
        limiteur.enregistrer_reponse(
            {"X-Rate-Limit-Remaining": "0", "X-Rate-Limit-Reset": reset}
        )
        debut = time.monotonic()
        limiteur.attendre_son_tour()
        self.assertGreater(time.monotonic() - debut, 0.2)

    def test_quota_restant_ne_declenche_rien(self):
        limiteur = api_insee.Limiteur(requetes=100, fenetre=60)
        limiteur.enregistrer_reponse({"X-Rate-Limit-Remaining": "12"})
        debut = time.monotonic()
        limiteur.attendre_son_tour()
        self.assertLess(time.monotonic() - debut, 0.2)

    def test_entetes_absents_ne_cassent_rien(self):
        limiteur = api_insee.Limiteur(requetes=100, fenetre=60)
        limiteur.enregistrer_reponse({})                       # API ouverte
        limiteur.enregistrer_reponse({"X-Rate-Limit-Remaining": "?"})
        debut = time.monotonic()
        limiteur.attendre_son_tour()
        self.assertLess(time.monotonic() - debut, 0.2)


class TestAttenteSur429(unittest.TestCase):
    """Un back-off de 2s puis 4s ne peut pas résoudre un quota par minute."""

    class FausseReponseHttp:
        def __init__(self, status_code, headers):
            self.status_code = status_code
            self.headers = headers

    def test_429_attend_la_reinitialisation_de_la_fenetre(self):
        reset = str(int((time.time() + 30) * 1000))
        reponse = self.FausseReponseHttp(429, {"X-Rate-Limit-Reset": reset})
        attente = api_insee._duree_attente(reponse, tentative=1)
        # Bien plus que les 2s du back-off exponentiel.
        self.assertGreater(attente, 20)

    def test_retry_after_prioritaire(self):
        reponse = self.FausseReponseHttp(429, {"Retry-After": "7"})
        self.assertEqual(api_insee._duree_attente(reponse, 1), 7.0)

    def test_erreur_serveur_garde_le_backoff(self):
        reponse = self.FausseReponseHttp(503, {})
        self.assertEqual(
            api_insee._duree_attente(reponse, 1), api_insee.ATTENTE_BASE
        )
        self.assertEqual(
            api_insee._duree_attente(reponse, 2), api_insee.ATTENTE_BASE * 2
        )

    def test_panne_reseau_sans_reponse(self):
        self.assertEqual(
            api_insee._duree_attente(None, 1), api_insee.ATTENTE_BASE
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
