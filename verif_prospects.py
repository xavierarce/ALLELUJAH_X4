#!/usr/bin/env python3
"""Vérificateur de prospects B2B — point d'entrée en ligne de commande.

Projet de fin de module « Scripting Python / Cybersécurité » — SUJET 4.

Besoin du client : avant tout démarchage, vérifier automatiquement pour chaque
prospect d'une liste si l'entreprise est **toujours en activité**, récupérer ses
**informations à jour**, et **signaler** celles qui ont cessé leur activité.

Usage :
    python3 verif_prospects.py donnees/prospects.csv
    python3 verif_prospects.py donnees/prospects.csv --sortie resultats/
    python3 verif_prospects.py donnees/prospects.csv --sequentiel   # pour comparer
    python3 verif_prospects.py donnees/prospects.csv --jours-recent 90 --verbose
    python3 verif_prospects.py donnees/prospects.csv --source insee  # nécessite une clé

Enchaînement des briques vues en cours :
    Séance 4  argparse + requests (appel API, vérification du status_code)
    Séance 5  regex + JSON (validation des SIREN, exploitation de la réponse)
    Séance 6  fichiers + try/except (lecture du CSV, écriture des livrables)
    Séance 7  ThreadPoolExecutor (parallélisation des appels — tâches I/O-bound)
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from verificateur import analyse, api_entreprises, api_insee, entrees, journal, sorties

# --- Sources de données interchangeables -------------------------------------
#
# Les deux clients exposent la même interface (`chercher_par_identifiant` et
# `chercher_par_nom`, mêmes arguments, même format de retour). Changer de source
# revient donc à changer de module, sans toucher au reste du programme.
#
# Leurs contraintes de débit sont en revanche à l'opposé l'une de l'autre, d'où
# un nombre de threads recommandé différent :
#   - Recherche d'entreprises : 7 requêtes/seconde  → on parallélise à 5
#   - INSEE Sirene            : 30 requêtes/minute  → on freine à 2
SOURCES = {
    "recherche": {
        "module": api_entreprises,
        "libelle": "API Recherche d'entreprises (ouverte, sans clé)",
        "workers_defaut": 5,
        "workers_max": 7,
    },
    "insee": {
        "module": api_insee,
        "libelle": "API Sirene INSEE (clé requise)",
        "workers_defaut": api_insee.WORKERS_RECOMMANDES,
        "workers_max": 5,
    },
}

SOURCE_DEFAUT = "recherche"

JOURS_RECENT_DEFAUT = 365
DOSSIER_SORTIE_DEFAUT = "resultats"


def verifier_prospect(prospect, source, jours_recent, aujourdhui):
    """Vérifie **un** prospect et renvoie sa fiche.

    C'est la fonction exécutée par chaque thread du pool. Elle ne lève jamais
    d'exception : un prospect en échec produit une fiche « ERREUR_API » et le
    traitement des autres continue. Sans ça, une seule API en vrac ferait
    perdre tout le lot.

    Args:
        prospect (entrees.Prospect): la ligne à vérifier.
        source (module): le client API à utiliser (`api_entreprises` ou
            `api_insee`) — les deux exposent la même interface.
        jours_recent (int): seuil de « cessation récente », en jours.
        aujourdhui (datetime.date): date de référence.

    Returns:
        dict: la fiche analysée, `a_signaler` déjà renseigné.
    """
    # 1. Identifiant présent mais incohérent : on rejette sans appeler l'API.
    if prospect.motif_rejet:
        logging.warning(f"{prospect.libelle} : {prospect.motif_rejet}")
        return analyse.marquer_a_signaler(analyse.fiche_identifiant_invalide(prospect))

    try:
        # 2. Chemin privilégié : recherche exacte par SIREN/SIRET.
        if prospect.identifiant_exploitable:
            logging.debug(f"{prospect.libelle} : recherche par identifiant")
            resultats = source.chercher_par_identifiant(prospect.identifiant)
            fiche = analyse.analyser_par_identifiant(
                prospect, resultats, jours_recent, aujourdhui
            )

        # 3. Repli : recherche approchée par dénomination.
        else:
            logging.debug(f"{prospect.libelle} : recherche par nom")
            candidats, total = source.chercher_par_nom(prospect.nom)
            fiche = analyse.analyser_par_nom(
                prospect, candidats, total, jours_recent, aujourdhui
            )

    except api_entreprises.ErreurAPI as erreur:
        logging.error(f"{prospect.libelle} : {erreur}")
        fiche = analyse.fiche_erreur_api(prospect, str(erreur))

    except Exception as erreur:  # noqa: BLE001 - filet de sécurité du thread
        # Un bug inattendu dans l'analyse ne doit pas tuer silencieusement un
        # thread : on le journalise et on le restitue comme une ligne en erreur.
        logging.exception(f"{prospect.libelle} : erreur inattendue")
        fiche = analyse.fiche_erreur_api(prospect, f"erreur interne : {erreur}")

    return analyse.marquer_a_signaler(fiche)


def verifier_lot(prospects, source, jours_recent, workers, aujourdhui):
    """Vérifie une liste de prospects, en parallèle ou en séquentiel.

    Les appels API sont **I/O-bound** : le programme passe son temps à attendre
    le réseau, pas à calculer. Les threads sont donc pertinents malgré le GIL
    (démontré en Séance 7).

    Args:
        prospects (list[entrees.Prospect]): les prospects à vérifier.
        source (module): le client API à utiliser.
        jours_recent (int): seuil de « cessation récente », en jours.
        workers (int): nombre de threads ; ``1`` force le mode séquentiel.
        aujourdhui (datetime.date): date de référence.

    Returns:
        list[dict]: les fiches, dans l'ordre du fichier d'entrée.
    """
    if workers <= 1:
        logging.info(f"Vérification séquentielle de {len(prospects)} prospect(s)")
        return [
            verifier_prospect(prospect, source, jours_recent, aujourdhui)
            for prospect in prospects
        ]

    logging.info(
        f"Vérification de {len(prospects)} prospect(s) sur {workers} threads"
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # `.map()` conserve l'ordre de la liste d'entrée : le CSV de sortie est
        # aligné sur le CSV d'entrée, ce qui facilite la relecture par le client.
        return list(
            executor.map(
                lambda prospect: verifier_prospect(
                    prospect, source, jours_recent, aujourdhui
                ),
                prospects,
            )
        )


def construire_parseur():
    """Déclare l'interface en ligne de commande (Séance 4, exo 3)."""
    parseur = argparse.ArgumentParser(
        description=(
            "Vérifie auprès de la base officielle des entreprises françaises "
            "si les prospects d'un fichier CSV sont toujours en activité."
        ),
        epilog="Exemple : python3 verif_prospects.py donnees/prospects.csv --verbose",
    )

    parseur.add_argument(
        "fichier",
        help="Fichier CSV des prospects (colonnes reconnues : nom, siren/siret, contact)",
    )
    parseur.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default=SOURCE_DEFAUT,
        help=(
            "Base officielle à interroger : « recherche » (ouverte, sans clé, "
            "défaut) ou « insee » (Sirene, nécessite la variable "
            f"d'environnement {api_insee.VARIABLE_CLE})"
        ),
    )
    parseur.add_argument(
        "--sortie",
        default=DOSSIER_SORTIE_DEFAUT,
        help=f"Dossier des livrables (défaut : {DOSSIER_SORTIE_DEFAUT}/)",
    )
    parseur.add_argument(
        "--jours-recent",
        type=int,
        default=JOURS_RECENT_DEFAUT,
        help=(
            "Une cessation de moins de N jours est signalée comme « récente » "
            f"(défaut : {JOURS_RECENT_DEFAUT})"
        ),
    )
    parseur.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Nombre d'appels API simultanés. Par défaut, adapté à la source : "
            f"{SOURCES['recherche']['workers_defaut']} pour « recherche » "
            f"(7 req/s autorisées), {SOURCES['insee']['workers_defaut']} pour "
            "« insee » (30 req/min seulement)"
        ),
    )
    parseur.add_argument(
        "--sequentiel",
        action="store_true",
        help="Désactive la parallélisation (équivaut à --workers 1)",
    )
    parseur.add_argument(
        "--limite",
        type=int,
        default=0,
        help="Ne traiter que les N premiers prospects (0 = tous, utile en démo)",
    )
    parseur.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche le détail de chaque appel API (niveau DEBUG)",
    )

    return parseur


def resoudre_workers(args, configuration, nombre_prospects):
    """Détermine le nombre de threads réellement utilisé.

    On borne la valeur demandée : en dessous de 1 elle n'a pas de sens, au-delà
    de la limite de la source choisie elle est contre-productive, et il est
    inutile de créer plus de threads que de prospects à traiter.

    Args:
        args (argparse.Namespace): les options de la ligne de commande.
        configuration (dict): l'entrée de `SOURCES` correspondant à la source.
        nombre_prospects (int): taille du lot à traiter.

    Returns:
        int: le nombre de threads à utiliser.
    """
    if args.sequentiel:
        return 1

    # Non précisé : on prend la valeur adaptée à la source choisie.
    workers = args.workers
    if workers is None:
        workers = configuration["workers_defaut"]

    maximum = configuration["workers_max"]
    if workers < 1:
        logging.warning(f"--workers {workers} invalide, ramené à 1")
        workers = 1
    elif workers > maximum:
        logging.warning(
            f"--workers {workers} dépasse ce que supporte la source "
            f"« {args.source} », ramené à {maximum}"
        )
        workers = maximum

    return min(workers, max(nombre_prospects, 1))


def main():
    args = construire_parseur().parse_args()
    journal.configurer(verbose=args.verbose)

    configuration = SOURCES[args.source]
    source = configuration["module"]
    logging.info(f"Source : {configuration['libelle']}")

    # --- 0. Vérification de la configuration, AVANT de lire quoi que ce soit -
    # La source INSEE exige une clé : mieux vaut échouer tout de suite avec un
    # message clair que prospect par prospect une fois le traitement lancé.
    try:
        if args.source == "insee":
            api_insee.cle_api()
    except api_insee.CleInseeManquante as erreur:
        logging.error(erreur)
        return 1

    # --- 1. Entrée : lecture et validation du CSV ---------------------------
    try:
        prospects = entrees.charger_prospects(args.fichier)
    except entrees.FichierProspectsInvalide as erreur:
        logging.error(erreur)
        # Code de retour non nul : le script est utilisable dans un cron / CI.
        return 1

    if args.limite > 0:
        prospects = prospects[: args.limite]
        logging.info(f"Limite appliquée : {len(prospects)} prospect(s) traité(s)")

    # --- 2. Traitement : appels API parallélisés -----------------------------
    workers = resoudre_workers(args, configuration, len(prospects))
    aujourdhui = date.today()

    debut = time.time()
    try:
        fiches = verifier_lot(
            prospects, source, args.jours_recent, workers, aujourdhui
        )
    except KeyboardInterrupt:
        # Ctrl+C pendant les appels réseau : on sort proprement, sans traceback.
        logging.warning("Interruption demandée — aucun livrable écrit")
        return 130
    duree = time.time() - debut

    # --- 3. Sortie : livrables + rapport console -----------------------------
    resume = analyse.compter(fiches)

    try:
        chemins = sorties.ecrire_livrables(
            fiches,
            resume,
            args.sortie,
            parametres={
                "fichier_entree": args.fichier,
                "source": args.source,
                "jours_recent": args.jours_recent,
                "workers": workers,
                "duree_secondes": round(duree, 2),
            },
            source_libelle=configuration["libelle"],
        )
    except sorties.ErreurEcriture as erreur:
        logging.error(erreur)
        return 1

    sorties.afficher_rapport(fiches, resume, duree, chemins)

    # Code 2 s'il reste des signalements : un cron peut déclencher une alerte.
    return 2 if resume["a_signaler"] else 0


if __name__ == "__main__":
    sys.exit(main())
