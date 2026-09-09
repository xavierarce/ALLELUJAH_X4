"""Lecture et validation du fichier JSON de prospects (Séances 5 et 6).

Format attendu — un tableau, un objet par prospect :

    [
      {"nom": "ORANGE", "siren": "380129866", "contact": "marie@cabinet.fr"},
      {"nom": "Société Générale"}
    ]

Clés reconnues : `nom`, `siren` **ou** `siret`, `contact`. Le module valide les
identifiants (regex + clé de Luhn) pour rejeter les fautes de frappe **sans
dépenser un appel API**, et ignore les entrées sans nom ni identifiant.
"""

import json
import logging
import re

# Séance 5 : les motifs regex qui valident la *forme* de l'identifiant.
MOTIF_SIREN = re.compile(r"^\d{9}$")
MOTIF_SIRET = re.compile(r"^\d{14}$")
MOTIF_NON_CHIFFRE = re.compile(r"\D+")

# Clés acceptées pour l'identifiant. La première renseignée gagne.
CLES_IDENTIFIANT = ("siren", "siret")


class FichierProspectsInvalide(Exception):
    """Le fichier d'entrée est inutilisable (absent, illisible, mal formé)."""


def cle_luhn_valide(numero):
    """Vérifie la clé de contrôle de Luhn d'un SIREN.

    Le dernier chiffre d'un SIREN est une clé de contrôle : en doublant un
    chiffre sur deux en partant de la droite, la somme obtenue doit être un
    multiple de 10.

    Args:
        numero (str): chaîne composée uniquement de chiffres.

    Returns:
        bool: ``True`` si la clé est cohérente.

    Examples:
        >>> cle_luhn_valide("380129866")   # ORANGE
        True
        >>> cle_luhn_valide("123456789")   # faute de frappe
        False
    """
    total = 0
    # On parcourt à l'envers : le rang 0 est le chiffre de contrôle.
    for rang, caractere in enumerate(reversed(numero)):
        chiffre = int(caractere)
        if rang % 2 == 1:            # un chiffre sur deux est doublé
            chiffre *= 2
            if chiffre > 9:
                chiffre -= 9         # équivaut à additionner les deux chiffres
        total += chiffre
    return total % 10 == 0


def _motif_rejet(chiffres, saisi):
    """Dit pourquoi un identifiant est inexploitable, ou ``None`` s'il est bon.

    Args:
        chiffres (str): l'identifiant réduit à ses chiffres.
        saisi (str): l'identifiant tel que saisi, pour le message.

    Returns:
        str | None: le motif de rejet, ou ``None``.
    """
    if not chiffres:
        # Pas d'identifiant : on cherchera par nom, ce n'est pas une erreur.
        return None

    if not (MOTIF_SIREN.match(chiffres) or MOTIF_SIRET.match(chiffres)):
        return (
            f"« {saisi} » n'est ni un SIREN (9 chiffres) "
            f"ni un SIRET (14 chiffres)"
        )

    if not cle_luhn_valide(chiffres[:9]):
        return (
            f"clé de contrôle invalide pour « {saisi} » (probable faute de frappe)"
        )

    return None


class Prospect:
    """Un prospect du fichier d'entrée, nettoyé et validé.

    Attributes:
        rang (int): position dans le tableau JSON, pour retrouver l'entrée.
        nom (str): nom de l'entreprise tel que saisi.
        contact (str): champ libre, repris à l'identique dans le rapport.
        identifiant_saisi (str): identifiant tel que saisi (peut être vide).
        siren (str): les 9 chiffres à envoyer à l'API, ``""`` si absent ou rejeté.
        motif_rejet (str | None): renseigné si l'identifiant est inexploitable.
    """

    def __init__(self, rang, nom, identifiant_saisi="", contact=""):
        self.rang = rang
        self.nom = str(nom or "").strip()
        self.contact = str(contact or "").strip()
        self.identifiant_saisi = str(identifiant_saisi or "").strip()

        # « 380 129 866 » ou « 380.129.866 » : on ne garde que les chiffres.
        chiffres = MOTIF_NON_CHIFFRE.sub("", self.identifiant_saisi)
        self.motif_rejet = _motif_rejet(chiffres, self.identifiant_saisi)

        # Les 9 premiers chiffres d'un SIRET sont son SIREN — et le SIREN est
        # le seul identifiant que l'API sait rechercher à l'identique.
        self.siren = "" if self.motif_rejet else chiffres[:9]

    @property
    def libelle(self):
        """Libellé court pour les logs et les messages."""
        return self.nom or self.identifiant_saisi or f"prospect n°{self.rang}"


def _lire_json(chemin):
    """Lit le fichier et renvoie la liste brute des prospects.

    Raises:
        FichierProspectsInvalide: fichier absent, illisible, ou JSON mal formé.
    """
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
        # On remonte la position : c'est ce qui rend une virgule oubliée
        # trouvable en dix secondes au lieu d'un quart d'heure.
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
    """Charge le fichier JSON et renvoie la liste des `Prospect`.

    Args:
        chemin (str): chemin du fichier JSON.

    Returns:
        list[Prospect]: les prospects exploitables, dans l'ordre du fichier.

    Raises:
        FichierProspectsInvalide: fichier absent, mal formé, ou sans aucun
            prospect exploitable.
    """
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
