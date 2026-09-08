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

from verificateur import analyse, api_entreprises, entrees, journal, sorties

# 7 requêtes/seconde maximum par IP côté API : 5 threads laissent de la marge
# tout en divisant le temps total par ~5. Monter plus haut ne fait que
# provoquer des HTTP 429 (et donc des attentes), pas gagner du temps.
WORKERS_DEFAUT = 5
WORKERS_MAX = 7

JOURS_RECENT_DEFAUT = 365
DOSSIER_SORTIE_DEFAUT = "resultats"


def verifier_prospect(prospect, jours_recent, aujourdhui):
    """Vérifie **un** prospect et renvoie sa fiche.

    C'est la fonction exécutée par chaque thread du pool. Elle ne lève jamais
    d'exception : un prospect en échec produit une fiche « ERREUR_API » et le
    traitement des autres continue. Sans ça, une seule API en vrac ferait
    perdre tout le lot.

    Args:
        prospect (entrees.Prospect): la ligne à vérifier.
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
            resultats = api_entreprises.chercher_par_identifiant(prospect.identifiant)
            fiche = analyse.analyser_par_identifiant(
                prospect, resultats, jours_recent, aujourdhui
            )

        # 3. Repli : recherche approchée par dénomination.
        else:
            logging.debug(f"{prospect.libelle} : recherche par nom")
            candidats, total = api_entreprises.chercher_par_nom(prospect.nom)
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


def verifier_lot(prospects, jours_recent, workers, aujourdhui):
    """Vérifie une liste de prospects, en parallèle ou en séquentiel.

    Les appels API sont **I/O-bound** : le programme passe son temps à attendre
    le réseau, pas à calculer. Les threads sont donc pertinents malgré le GIL
    (démontré en Séance 7).

    Args:
        prospects (list[entrees.Prospect]): les prospects à vérifier.
        jours_recent (int): seuil de « cessation récente », en jours.
        workers (int): nombre de threads ; ``1`` force le mode séquentiel.
        aujourdhui (datetime.date): date de référence.

    Returns:
        list[dict]: les fiches, dans l'ordre du fichier d'entrée.
    """
    if workers <= 1:
        logging.info(f"Vérification séquentielle de {len(prospects)} prospect(s)")
        return [
            verifier_prospect(prospect, jours_recent, aujourdhui)
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
                lambda prospect: verifier_prospect(prospect, jours_recent, aujourdhui),
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
        default=WORKERS_DEFAUT,
        help=(
            f"Nombre d'appels API simultanés, 1 à {WORKERS_MAX} "
            f"(défaut : {WORKERS_DEFAUT} ; l'API plafonne à 7 requêtes/s)"
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


def resoudre_workers(args, nombre_prospects):
    """Détermine le nombre de threads réellement utilisé.

    On borne la valeur demandée : en dessous de 1 elle n'a pas de sens, au-delà
    de la limite de l'API elle est contre-productive, et il est inutile de créer
    plus de threads que de prospects à traiter.
    """
    if args.sequentiel:
        return 1

    workers = args.workers
    if workers < 1:
        logging.warning(f"--workers {workers} invalide, ramené à 1")
        workers = 1
    elif workers > WORKERS_MAX:
        logging.warning(
            f"--workers {workers} dépasse la limite de l'API "
            f"({WORKERS_MAX} requêtes/s), ramené à {WORKERS_MAX}"
        )
        workers = WORKERS_MAX

    return min(workers, max(nombre_prospects, 1))


def main():
    args = construire_parseur().parse_args()
    journal.configurer(verbose=args.verbose)

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
    workers = resoudre_workers(args, len(prospects))
    aujourdhui = date.today()

    debut = time.time()
    try:
        fiches = verifier_lot(prospects, args.jours_recent, workers, aujourdhui)
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
                "jours_recent": args.jours_recent,
                "workers": workers,
                "duree_secondes": round(duree, 2),
            },
        )
    except sorties.ErreurEcriture as erreur:
        logging.error(erreur)
        return 1

    sorties.afficher_rapport(fiches, resume, duree, chemins)

    # Code 2 s'il reste des signalements : un cron peut déclencher une alerte.
    return 2 if resume["a_signaler"] else 0


if __name__ == "__main__":
    sys.exit(main())
