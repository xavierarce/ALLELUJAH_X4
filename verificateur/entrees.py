"""Lecture et validation du fichier JSON de prospects.

Format attendu — un tableau d'objets, clés `nom`, `siren` ou `siret`, `contact` :

    [{"nom": "ORANGE", "siren": "380129866", "contact": "marie@cabinet.fr"}]
"""

import json
import logging
import re

MOTIF_SIREN = re.compile(r"^\d{9}$")
MOTIF_SIRET = re.compile(r"^\d{14}$")
MOTIF_NON_CHIFFRE = re.compile(r"\D+")

# La première clé renseignée gagne.
CLES_IDENTIFIANT = ("siren", "siret")


class FichierProspectsInvalide(Exception):
    """Le fichier d'entrée est inutilisable (absent, illisible, mal formé)."""


def cle_luhn_valide(numero):
    """Vérifie la clé de contrôle portée par le dernier chiffre d'un SIREN."""
    total = 0
    # Le rang 0 est le chiffre de contrôle, d'où le parcours à l'envers.
    for rang, caractere in enumerate(reversed(numero)):
        chiffre = int(caractere)
        if rang % 2 == 1:
            chiffre *= 2
            if chiffre > 9:
                chiffre -= 9
        total += chiffre
    return total % 10 == 0


def _motif_rejet(chiffres, saisi):
    """Dit pourquoi un identifiant est inexploitable, ou ``None`` s'il est bon."""
    if not chiffres:
        # Pas d'identifiant : on cherchera par nom, ce n'est pas une erreur.
        return None

    if not (MOTIF_SIREN.match(chiffres) or MOTIF_SIRET.match(chiffres)):
        return (
            f"« {saisi} » n'est ni un SIREN (9 chiffres) ni un SIRET (14 chiffres)"
        )

    if not cle_luhn_valide(chiffres[:9]):
        return f"clé de contrôle invalide pour « {saisi} » (probable faute de frappe)"

    return None


class Prospect:
    """Un prospect du fichier d'entrée, nettoyé et validé.

    `siren` porte les 9 chiffres à envoyer à l'API, ou ``""`` si l'identifiant
    est absent ou rejeté — dans ce dernier cas `motif_rejet` dit pourquoi.
    """

    def __init__(self, rang, nom, identifiant_saisi="", contact=""):
        self.rang = rang
        self.nom = str(nom or "").strip()
        self.contact = str(contact or "").strip()
        self.identifiant_saisi = str(identifiant_saisi or "").strip()

        chiffres = MOTIF_NON_CHIFFRE.sub("", self.identifiant_saisi)
        self.motif_rejet = _motif_rejet(chiffres, self.identifiant_saisi)

        # Les 9 premiers chiffres d'un SIRET sont son SIREN, et le SIREN est le
        # seul identifiant que l'API sait rechercher à l'identique.
        self.siren = "" if self.motif_rejet else chiffres[:9]

    @property
    def libelle(self):
        """Libellé court pour les logs et les messages."""
        return self.nom or self.identifiant_saisi or f"prospect n°{self.rang}"


def _lire_json(chemin):
    """Lit le fichier et renvoie la liste brute des prospects."""
    try:
        with open(chemin, "r", encoding="utf-8") as fichier:
            entrees = json.load(fichier)
    except FileNotFoundError:
        raise FichierProspectsInvalide(f"Fichier introuvable : {chemin}") from None
    except PermissionError:
        raise FichierProspectsInvalide(f"Accès refusé : {chemin}") from None
    except UnicodeDecodeError:
        raise FichierProspectsInvalide(
            f"{chemin} n'est pas encodé en UTF-8 — le réenregistrer en UTF-8."
        ) from None
    except json.JSONDecodeError as erreur:
        # La position rend une virgule oubliée trouvable en dix secondes.
        raise FichierProspectsInvalide(
            f"{chemin} n'est pas du JSON valide — {erreur.msg} "
            f"(ligne {erreur.lineno}, colonne {erreur.colno})."
        ) from None

    if not isinstance(entrees, list):
        raise FichierProspectsInvalide(
            f"{chemin} doit contenir un tableau de prospects."
        )

    return entrees


def charger_prospects(chemin):
    """Charge le fichier JSON et renvoie la liste des `Prospect` exploitables."""
    prospects = []
    ignores = 0

    for rang, entree in enumerate(_lire_json(chemin), start=1):
        if not isinstance(entree, dict):
            logging.warning(f"Prospect n°{rang} ignoré : ce n'est pas un objet JSON")
            ignores += 1
            continue

        prospect = Prospect(
            rang=rang,
            nom=entree.get("nom"),
            identifiant_saisi=next(
                (entree[cle] for cle in CLES_IDENTIFIANT if entree.get(cle)), ""
            ),
            contact=entree.get("contact"),
        )

        if not prospect.nom and not prospect.identifiant_saisi:
            logging.warning(f"Prospect n°{rang} ignoré : ni nom ni identifiant")
            ignores += 1
            continue

        prospects.append(prospect)

    if not prospects:
        raise FichierProspectsInvalide(
            f"{chemin} ne contient aucun prospect exploitable "
            f"({ignores} entrée(s) inutilisable(s))."
        )

    logging.info(
        f"{len(prospects)} prospect(s) chargé(s) depuis {chemin}"
        + (f" — {ignores} entrée(s) ignorée(s)" if ignores else "")
    )
    return prospects
