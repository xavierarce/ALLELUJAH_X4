"""Écriture du livrable JSON (Séance 6 : fichiers + try/except).

Une seule sortie, `resultats/rapport.json` :

    {
      "meta":      { horodatage, source, paramètres, compteurs },
      "prospects": [ une fiche par prospect vérifié ]
    }

Chaque fiche porte `a_signaler` (booléen) et `alerte`, ce qui donne le
« signalement clair » demandé par le client : les prospects à ne pas démarcher
s'extraient en une ligne, sans relire le rapport.

    jq '.prospects[] | select(.a_signaler)' resultats/rapport.json
    python3 -c "import json;print([p['nom_officiel'] for p in
                json.load(open('resultats/rapport.json'))['prospects']
                if p['a_signaler']])"

Le JSON est écrit avec `ensure_ascii=False` (accents lisibles dans le fichier)
et `indent=2` (relisible à l'œil, et un diff reste exploitable d'une exécution
à l'autre).
"""

import json
import logging
import os
from datetime import datetime

NOM_JSON = "rapport.json"
SOURCE = "API Recherche d'entreprises (recherche-entreprises.api.gouv.fr)"


class ErreurEcriture(Exception):
    """L'écriture du livrable a échoué (droits, disque plein, chemin)."""


def ecrire_rapport(fiches, resume, dossier, parametres):
    """Écrit le rapport JSON et renvoie son chemin.

    Args:
        fiches (list[dict]): les fiches analysées.
        resume (dict): les compteurs produits par `analyse.compter()`.
        dossier (str): dossier de sortie, créé s'il n'existe pas.
        parametres (dict): paramètres d'exécution, tracés dans le rapport pour
            le rendre auditable.

    Returns:
        str: le chemin du fichier écrit.

    Raises:
        ErreurEcriture: si le dossier ou le fichier n'a pas pu être écrit.
    """
    try:
        os.makedirs(dossier, exist_ok=True)
    except OSError as erreur:
        raise ErreurEcriture(f"Dossier {dossier} impossible : {erreur}") from erreur

    chemin = os.path.join(dossier, NOM_JSON)

    rapport = {
        "meta": {
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "source": SOURCE,
            "parametres": parametres,
            "resume": resume,
        },
        "prospects": fiches,
    }

    # Séance 5 : json.dump.
    try:
        with open(chemin, "w", encoding="utf-8") as fichier:
            json.dump(rapport, fichier, ensure_ascii=False, indent=2)
            fichier.write("\n")
    except OSError as erreur:
        raise ErreurEcriture(f"Écriture impossible sur {chemin} : {erreur}") from erreur

    logging.info(
        f"{len(fiches)} fiche(s) écrites, dont {resume['a_signaler']} à signaler"
    )
    return chemin


def afficher_rapport(fiches, resume, duree, chemin):
    """Affiche la synthèse sur stdout, à destination de l'opérateur.

    Les logs partent sur stderr : ce rapport est donc seul sur stdout et peut
    être redirigé vers un fichier ou envoyé par mail.
    """
    largeur = 74
    print("\n" + "=" * largeur)
    print("  RAPPORT DE VÉRIFICATION DES PROSPECTS")
    print("=" * largeur)
    print(f"\n  Prospects traités : {resume['total']}  (en {duree:.2f} s)")

    print("\n  État d'activité :")
    for etat, nombre in sorted(resume["par_etat"].items()):
        print(f"    - {etat:<20} {nombre}")

    signalements = [fiche for fiche in fiches if fiche["a_signaler"]]
    if signalements:
        print("\n" + "-" * largeur)
        print(f"  /!\\  {len(signalements)} PROSPECT(S) À NE PAS DÉMARCHER SANS VÉRIFIER")
        print("-" * largeur)
        for fiche in signalements:
            nom = fiche["nom_officiel"] or fiche["nom_saisi"] or "?"
            print(f"    • [n°{fiche['rang']}] {nom} — {fiche['alerte']}")
            print(f"      {fiche['message']}")
    else:
        print("\n  OK — aucun signalement : tous les prospects sont actifs.")

    print("\n" + "-" * largeur)
    print(f"  Livrable : {chemin}")
    print("=" * largeur + "\n")
