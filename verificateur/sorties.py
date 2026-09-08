"""Écriture des livrables (Séance 6 : fichiers + try/except).

Trois sorties, chacune pour un usage précis :

  - ``prospects_verifies.csv`` — le tableau complet, une ligne par prospect,
    ouvrable directement dans Excel ou LibreOffice ;
  - ``alertes.csv`` — le sous-ensemble à traiter en priorité (c'est le
    « signalement clair » demandé par le client) ;
  - ``rapport.json`` — les mêmes données plus un bloc ``meta`` (horodatage,
    paramètres, compteurs) pour un usage automatisé en aval.

Le CSV est écrit avec un **BOM UTF-8** (`utf-8-sig`) et le point-virgule comme
séparateur : sans ça, Excel en configuration française casse les accents et
empile tout dans une seule colonne.
"""

import csv
import json
import logging
import os
from datetime import datetime

# Ordre des colonnes du CSV. Fixé ici (et pas déduit des dictionnaires) pour
# que le fichier livré ait toujours la même structure, quoi qu'il arrive.
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
    "code_postal",
    "commune",
    "date_creation",
    "date_cessation",
    "jours_depuis_cessation",
    "cessation_recente",
    "etat_siege",
    "etablissements_ouverts",
    "tranche_effectif",
    "activite_principale",
    "score_correspondance",
    "candidats_alternatifs",
    "fraicheur_donnee",
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
    que « True »/« False », et trié correctement par Excel.
    """
    if valeur is True:
        return "oui"
    if valeur is False:
        return "non"
    if valeur is None:
        return ""
    return valeur


def preparer_dossier(dossier):
    """Crée le dossier de sortie s'il n'existe pas.

    Raises:
        ErreurEcriture: si le dossier ne peut pas être créé.
    """
    try:
        os.makedirs(dossier, exist_ok=True)
    except OSError as erreur:
        raise ErreurEcriture(
            f"Impossible de créer le dossier {dossier} : {erreur}"
        ) from erreur


def _ecrire_csv(chemin, fiches):
    """Écrit une liste de fiches dans un CSV aux colonnes fixes."""
    try:
        with open(chemin, "w", encoding=ENCODAGE_CSV, newline="") as fichier:
            redacteur = csv.DictWriter(
                fichier,
                fieldnames=COLONNES,
                delimiter=SEPARATEUR_CSV,
                # `extrasaction="ignore"` : une clé en trop dans une fiche ne
                # doit pas faire échouer l'écriture de tout le livrable.
                extrasaction="ignore",
            )
            redacteur.writeheader()
            for fiche in fiches:
                redacteur.writerow(
                    {colonne: _valeur_csv(fiche.get(colonne)) for colonne in COLONNES}
                )
    except PermissionError as erreur:
        raise ErreurEcriture(f"Accès refusé en écriture sur {chemin}") from erreur
    except OSError as erreur:
        raise ErreurEcriture(f"Écriture impossible sur {chemin} : {erreur}") from erreur


def ecrire_livrables(fiches, resume, dossier, parametres, source_libelle=None):
    """Écrit les trois livrables et renvoie leurs chemins.

    Args:
        fiches (list[dict]): les fiches analysées.
        resume (dict): les compteurs produits par `analyse.compter()`.
        dossier (str): dossier de sortie.
        parametres (dict): paramètres d'exécution, tracés dans le JSON.
        source_libelle (str | None): base officielle interrogée, tracée dans le
            bloc ``meta`` pour rendre le rapport auditable.

    Returns:
        dict: les chemins écrits, par clé (`csv`, `alertes`, `json`).

    Raises:
        ErreurEcriture: si un fichier n'a pas pu être écrit.
    """
    preparer_dossier(dossier)

    chemin_csv = os.path.join(dossier, NOM_CSV)
    chemin_alertes = os.path.join(dossier, NOM_ALERTES)
    chemin_json = os.path.join(dossier, NOM_JSON)

    _ecrire_csv(chemin_csv, fiches)
    logging.info(f"Livrable écrit : {chemin_csv} ({len(fiches)} ligne(s))")

    alertes = [f for f in fiches if f["a_signaler"]]
    _ecrire_csv(chemin_alertes, alertes)
    logging.info(f"Livrable écrit : {chemin_alertes} ({len(alertes)} alerte(s))")

    # Séance 5 : json.dump. `ensure_ascii=False` pour garder les accents
    # lisibles dans le fichier, `indent=2` pour qu'il reste relisible à l'œil.
    rapport = {
        "meta": {
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "source": source_libelle or "non précisée",
            "parametres": parametres,
            "resume": resume,
        },
        "prospects": fiches,
    }

    try:
        with open(chemin_json, "w", encoding="utf-8") as fichier:
            json.dump(rapport, fichier, ensure_ascii=False, indent=2)
    except PermissionError as erreur:
        raise ErreurEcriture(
            f"Accès refusé en écriture sur {chemin_json}"
        ) from erreur
    except OSError as erreur:
        raise ErreurEcriture(
            f"Écriture impossible sur {chemin_json} : {erreur}"
        ) from erreur

    logging.info(f"Livrable écrit : {chemin_json}")

    return {"csv": chemin_csv, "alertes": chemin_alertes, "json": chemin_json}


LARGEUR_NOM = 34


def _tronquer(texte, largeur=LARGEUR_NOM):
    """Coupe un texte trop long en signalant la coupure par « … »."""
    if len(texte) <= largeur:
        return texte
    return texte[: largeur - 1] + "…"


def _ligne_tableau(fiche):
    """Formate une fiche en une ligne du tableau console."""
    nom = _tronquer(fiche["nom_officiel"] or fiche["nom_saisi"] or "?")
    etat = fiche["etat_activite"]
    alerte = fiche["alerte"] or "-"
    return f"  {nom:<{LARGEUR_NOM}} {fiche['siren']:<10} {etat:<8} {alerte}"


def afficher_rapport(fiches, resume, duree, chemins):
    """Affiche la synthèse sur stdout, à destination de l'opérateur.

    Les logs partent sur stderr (cf. `journal.py`) : ce rapport est donc seul
    sur stdout et peut être redirigé vers un fichier ou un mail.
    """
    largeur = 78
    print("\n" + "=" * largeur)
    print("  RAPPORT DE VÉRIFICATION DES PROSPECTS")
    print("=" * largeur)

    print(f"\n  Prospects traités : {resume['total']}")
    print(f"  Durée            : {duree:.2f} s")

    print("\n  État d'activité :")
    for etat, nombre in sorted(resume["par_etat"].items()):
        print(f"    - {etat:<24} {nombre}")

    print("\n  Qualité de la vérification :")
    for statut, nombre in sorted(resume["par_statut"].items()):
        print(f"    - {statut:<24} {nombre}")

    signalements = [f for f in fiches if f["a_signaler"]]

    if signalements:
        print(f"\n{'-' * largeur}")
        print(f"  ⚠  {len(signalements)} PROSPECT(S) À NE PAS DÉMARCHER SANS VÉRIFIER")
        print(f"{'-' * largeur}")
        print(f"  {'ENTREPRISE':<{LARGEUR_NOM}} {'SIREN':<10} {'ÉTAT':<8} ALERTE")
        for fiche in signalements:
            print(_ligne_tableau(fiche))

        print("\n  Détail :")
        for fiche in signalements:
            nom = fiche["nom_officiel"] or fiche["nom_saisi"] or "?"
            print(f"    • [ligne {fiche['numero_ligne']}] {nom}")
            print(f"      {fiche['message']}")
    else:
        print("\n  ✓ Aucun signalement : tous les prospects sont actifs et vérifiés.")

    print(f"\n{'-' * largeur}")
    print("  Livrables :")
    for etiquette, chemin in chemins.items():
        print(f"    - {etiquette:<9} {chemin}")
    print("=" * largeur + "\n")
