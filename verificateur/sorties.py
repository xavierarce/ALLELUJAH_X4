"""Écriture des livrables (Séance 6 : fichiers + try/except).

Trois sorties, chacune pour un usage précis :

  - ``prospects_verifies.csv`` — le tableau complet, une ligne par prospect,
    ouvrable directement dans Excel ou LibreOffice ;
  - ``alertes.csv`` — le sous-ensemble à traiter en priorité : c'est le
    « signalement clair » demandé par le client ;
  - ``rapport.json`` — les mêmes données plus un bloc ``meta`` (horodatage,
    paramètres, compteurs), pour un usage automatisé en aval.

Le CSV est écrit avec un **BOM UTF-8** (`utf-8-sig`) et le point-virgule comme
séparateur : sans ça, Excel en configuration française casse les accents et
empile tout dans une seule colonne.
"""

import csv
import json
import logging
import os
from datetime import datetime

# Ordre des colonnes du CSV. Fixé ici (et pas déduit des dictionnaires) pour que
# le fichier livré ait toujours la même structure, quoi qu'il arrive.
COLONNES = [
    "numero_ligne",
    "nom_saisi",
    "identifiant_saisi",
    "contact",
    "statut_verification",
    "etat_activite",
    "alerte",
    "a_signaler",
    "message",
    "nom_officiel",
    "siren",
    "siret_siege",
    "adresse_siege",
    "date_creation",
    "date_cessation",
    "score_correspondance",
    "url_annuaire",
]

SEPARATEUR_CSV = ";"
ENCODAGE_CSV = "utf-8-sig"

NOM_CSV = "prospects_verifies.csv"
NOM_ALERTES = "alertes.csv"
NOM_JSON = "rapport.json"


class ErreurEcriture(Exception):
    """L'écriture d'un livrable a échoué (droits, disque plein, chemin)."""


def _valeur_csv(valeur):
    """Convertit une valeur Python en texte lisible dans un tableur.

    Les booléens deviennent « oui »/« non » : plus parlant pour un commercial
    que « True »/« False ».
    """
    if valeur is True:
        return "oui"
    if valeur is False:
        return "non"
    return "" if valeur is None else valeur


def _ecrire_csv(chemin, fiches):
    """Écrit une liste de fiches dans un CSV aux colonnes fixes.

    Raises:
        ErreurEcriture: si le fichier n'a pas pu être écrit.
    """
    try:
        with open(chemin, "w", encoding=ENCODAGE_CSV, newline="") as fichier:
            redacteur = csv.DictWriter(
                fichier, fieldnames=COLONNES, delimiter=SEPARATEUR_CSV
            )
            redacteur.writeheader()
            for fiche in fiches:
                redacteur.writerow(
                    {colonne: _valeur_csv(fiche.get(colonne)) for colonne in COLONNES}
                )
    except OSError as erreur:
        raise ErreurEcriture(f"Écriture impossible sur {chemin} : {erreur}") from erreur


def ecrire_livrables(fiches, resume, dossier, parametres):
    """Écrit les trois livrables et renvoie leurs chemins.

    Args:
        fiches (list[dict]): les fiches analysées.
        resume (dict): les compteurs produits par `analyse.compter()`.
        dossier (str): dossier de sortie, créé s'il n'existe pas.
        parametres (dict): paramètres d'exécution, tracés dans le JSON pour
            rendre le rapport auditable.

    Returns:
        dict: les chemins écrits, par clé (`csv`, `alertes`, `json`).

    Raises:
        ErreurEcriture: si un fichier n'a pas pu être écrit.
    """
    try:
        os.makedirs(dossier, exist_ok=True)
    except OSError as erreur:
        raise ErreurEcriture(f"Dossier {dossier} impossible : {erreur}") from erreur

    chemins = {
        "csv": os.path.join(dossier, NOM_CSV),
        "alertes": os.path.join(dossier, NOM_ALERTES),
        "json": os.path.join(dossier, NOM_JSON),
    }

    _ecrire_csv(chemins["csv"], fiches)

    alertes = [fiche for fiche in fiches if fiche["a_signaler"]]
    _ecrire_csv(chemins["alertes"], alertes)

    # Séance 5 : json.dump. `ensure_ascii=False` garde les accents lisibles
    # dans le fichier, `indent=2` le laisse relisible à l'œil.
    rapport = {
        "meta": {
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "source": "API Recherche d'entreprises (recherche-entreprises.api.gouv.fr)",
            "parametres": parametres,
            "resume": resume,
        },
        "prospects": fiches,
    }

    try:
        with open(chemins["json"], "w", encoding="utf-8") as fichier:
            json.dump(rapport, fichier, ensure_ascii=False, indent=2)
    except OSError as erreur:
        raise ErreurEcriture(
            f"Écriture impossible sur {chemins['json']} : {erreur}"
        ) from erreur

    logging.info(f"{len(fiches)} ligne(s) écrites, dont {len(alertes)} alerte(s)")
    return chemins


def afficher_rapport(fiches, resume, duree, chemins):
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
            print(f"    • [ligne {fiche['numero_ligne']}] {nom} — {fiche['alerte']}")
            print(f"      {fiche['message']}")
    else:
        print("\n  OK — aucun signalement : tous les prospects sont actifs.")

    print("\n" + "-" * largeur)
    print("  Livrables :")
    for etiquette, chemin in chemins.items():
        print(f"    - {etiquette:<8} {chemin}")
    print("=" * largeur + "\n")
