#!/usr/bin/env python3
"""Vérificateur de prospects B2B — point d'entrée en ligne de commande.

Pour chaque prospect d'une liste JSON, vérifie auprès de la base officielle des
entreprises françaises si la société est toujours en activité, récupère ses
informations à jour, et signale celles qui ont cessé leur activité.

    python3 verif_prospects.py donnees/prospects.json
    python3 verif_prospects.py donnees/prospects.json --sequentiel --verbose
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from verificateur import analyse, api, entrees, sorties

DOSSIER_SORTIE_DEFAUT = "resultats"

# L'API autorise 7 requêtes/seconde par adresse IP : c'est elle qui plafonne le
# parallélisme, pas la machine. On reste en dessous pour ne pas se faire jeter.
WORKERS = 5


def verifier_prospect(prospect):
    """Vérifie un prospect et renvoie sa fiche.

    Exécutée par chaque thread du pool, elle ne lève jamais d'exception : un
    prospect en échec produit une fiche « ERREUR_API » et les autres continuent.
    Sans ça, une seule API en vrac ferait perdre tout le lot.
    """
    if prospect.motif_rejet:
        logging.warning(f"{prospect.libelle} : {prospect.motif_rejet}")
        return analyse.fiche_identifiant_invalide(prospect)

    try:
        resultats = api.chercher(prospect.siren or prospect.nom)
        return analyse.analyser(prospect, resultats)

    except api.ErreurAPI as erreur:
        logging.error(f"{prospect.libelle} : {erreur}")
        return analyse.fiche_erreur_api(prospect, erreur)

    except Exception as erreur:
        # Un bug inattendu ne doit pas tuer silencieusement un thread.
        logging.error(f"{prospect.libelle} : erreur inattendue — {erreur}")
        return analyse.fiche_erreur_api(prospect, f"erreur interne : {erreur}")


def verifier_lot(prospects, workers):
    """Vérifie les prospects en parallèle et renvoie les fiches dans l'ordre.

    Les appels API sont I/O-bound : le programme attend le réseau au lieu de
    calculer, les threads sont donc utiles malgré le GIL. Avec `workers=1`, le
    pool exécute les appels un par un — c'est le mode séquentiel.
    """
    logging.info(f"Vérification de {len(prospects)} prospect(s) sur {workers} thread(s)")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # `.map()` conserve l'ordre d'entrée : le rapport reste aligné sur le
        # fichier du cabinet.
        return list(executor.map(verifier_prospect, prospects))


def analyser_arguments():
    """Déclare et lit l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description=(
            "Vérifie auprès de la base officielle des entreprises françaises "
            "si les prospects d'un fichier JSON sont toujours en activité."
        ),
        epilog="Exemple : python3 verif_prospects.py donnees/prospects.json",
    )
    parseur.add_argument(
        "fichier",
        help="Fichier JSON des prospects (clés reconnues : nom, siren/siret, contact)",
    )
    parseur.add_argument(
        "--sortie",
        default=DOSSIER_SORTIE_DEFAUT,
        help=f"Dossier du rapport (défaut : {DOSSIER_SORTIE_DEFAUT}/)",
    )
    parseur.add_argument(
        "--sequentiel",
        action="store_true",
        help="Un seul appel API à la fois, pour mesurer le gain des threads",
    )
    parseur.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche le détail de chaque appel API (niveau DEBUG)",
    )
    return parseur.parse_args()


def main():
    args = analyser_arguments()

    # Logs sur stderr : la synthèse finale reste seule sur stdout et peut donc
    # être redirigée (`> rapport.txt`).
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    try:
        prospects = entrees.charger_prospects(args.fichier)
    except entrees.FichierProspectsInvalide as erreur:
        logging.error(erreur)
        # Code non nul : le script est utilisable dans un cron.
        return 1

    if args.sequentiel:
        workers = 1
    elif len(prospects) < WORKERS:
        # Inutile d'ouvrir plus de threads que de prospects à traiter.
        workers = len(prospects)
    else:
        workers = WORKERS

    debut = time.time()
    try:
        fiches = verifier_lot(prospects, workers)
    except KeyboardInterrupt:
        logging.warning("Interruption demandée — aucun rapport écrit")
        return 130
    duree = time.time() - debut

    resume = analyse.compter(fiches)
    try:
        chemin = sorties.ecrire_rapport(
            fiches,
            resume,
            args.sortie,
            parametres={
                "fichier_entree": args.fichier,
                "workers": workers,
                # Deux décimales suffisent, et le JSON reste lisible.
                "duree_secondes": float(f"{duree:.2f}"),
            },
        )
    except sorties.ErreurEcriture as erreur:
        logging.error(erreur)
        return 1

    sorties.afficher_synthese(fiches, resume, duree, chemin)

    # Code 2 s'il reste des signalements : un cron peut déclencher une alerte.
    return 2 if resume["a_signaler"] else 0


if __name__ == "__main__":
    sys.exit(main())
