"""Écriture du livrable `resultats/rapport.json`.

    { "meta": { horodatage, source, paramètres, compteurs },
      "prospects": [ une fiche par prospect vérifié ] }

Une fiche dont `alerte` est vide n'a rien à signaler ; les autres sont les
prospects à ne pas démarcher, extractibles en une ligne :

    jq '.prospects[] | select(.alerte != "")' resultats/rapport.json
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

    `parametres` trace les options d'exécution, ce qui rend le rapport auditable.
    """
    rapport = {
        "meta": {
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "source": SOURCE,
            "parametres": parametres,
            "resume": resume,
        },
        "prospects": fiches,
    }

    chemin = os.path.join(dossier, NOM_JSON)
    try:
        os.makedirs(dossier, exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as fichier:
            # Accents lisibles dans le fichier, et un diff exploitable d'une
            # exécution à l'autre.
            json.dump(rapport, fichier, ensure_ascii=False, indent=2)
            fichier.write("\n")
    except OSError as erreur:
        raise ErreurEcriture(f"Écriture impossible sur {chemin} : {erreur}") from erreur

    logging.info(
        f"{len(fiches)} fiche(s) écrites, dont {resume['a_signaler']} à signaler"
    )
    return chemin


def afficher_synthese(fiches, resume, duree, chemin):
    """Affiche la synthèse sur stdout, à destination de l'opérateur.

    Les logs partent sur stderr : la synthèse est donc seule sur stdout et peut
    être redirigée ou envoyée par mail.
    """
    largeur = 74
    print("\n" + "=" * largeur)
    print("  RAPPORT DE VÉRIFICATION DES PROSPECTS")
    print("=" * largeur)
    print(f"\n  Prospects traités : {resume['total']}  (en {duree:.2f} s)")

    print("\n  État d'activité :")
    for etat, nombre in sorted(resume["par_etat"].items()):
        print(f"    - {etat:<20} {nombre}")

    signalements = [fiche for fiche in fiches if fiche["alerte"]]
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
