"""Lecture et validation du fichier JSON de prospects (Séances 5 et 6).

Le cabinet nous fournit sa liste en JSON : un tableau d'objets, un objet par
prospect. Ce module absorbe les saisies approximatives et ne laisse remonter
que des objets `Prospect` exploitables — ou un motif de rejet explicite.

Format attendu :

    [
      {"nom": "ORANGE", "siren": "380129866", "contact": "marie@cabinet.fr"},
      {"nom": "Société Générale"}
    ]

Règles retenues :
  - le fichier peut être le tableau directement, ou un objet
    ``{"prospects": [...]}`` — les deux formes se rencontrent ;
  - la clé de l'identifiant peut être ``siren`` ou ``siret`` ;
  - un identifiant est un SIREN (9 chiffres) ou un SIRET (14 chiffres), dont on
    vérifie la **clé de Luhn** : ça rejette les fautes de frappe sans dépenser
    un appel API ;
  - un prospect sans nom **et** sans identifiant est ignoré (avec un warning).
"""

import json
import logging
import re

# Séance 5 : les motifs regex qui valident la *forme* de l'identifiant.
MOTIF_SIREN = re.compile(r"^\d{9}$")
MOTIF_SIRET = re.compile(r"^\d{14}$")
MOTIF_NON_CHIFFRE = re.compile(r"\D+")

# Clés acceptées pour l'identifiant. La première présente gagne.
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


class Prospect:
    """Un prospect du fichier d'entrée, nettoyé et validé.

    Attributes:
        rang (int): position dans le tableau JSON (pour retrouver la ligne).
        nom (str): nom de l'entreprise tel que saisi.
        contact (str): champ libre, repris à l'identique dans la sortie.
        identifiant_saisi (str): identifiant tel que saisi (peut être vide).
        identifiant (str): identifiant réduit aux chiffres, ``""`` si absent.
        motif_rejet (str | None): renseigné si l'identifiant est inexploitable.
    """

    def __init__(self, rang, nom, identifiant_saisi="", contact=""):
        self.rang = rang
        self.nom = str(nom or "").strip()
        self.contact = str(contact or "").strip()
        self.identifiant_saisi = str(identifiant_saisi or "").strip()
        # « 380 129 866 » ou « 380.129.866 » : on ne garde que les chiffres.
        self.identifiant = MOTIF_NON_CHIFFRE.sub("", self.identifiant_saisi)
        self.motif_rejet = self._verifier_identifiant()

    def _verifier_identifiant(self):
        """Renvoie le motif de rejet de l'identifiant, ou ``None`` s'il est bon."""
        if not self.identifiant:
            # Pas d'identifiant : on cherchera par nom, ce n'est pas une erreur.
            return None

        if not (MOTIF_SIREN.match(self.identifiant)
                or MOTIF_SIRET.match(self.identifiant)):
            return (
                f"« {self.identifiant_saisi} » n'est ni un SIREN (9 chiffres) "
                f"ni un SIRET (14 chiffres)"
            )

        # La forme est bonne : on contrôle la clé. Les 9 premiers chiffres d'un
        # SIRET forment le SIREN, qui porte la clé de Luhn.
        if not cle_luhn_valide(self.identifiant[:9]):
            return (
                f"clé de contrôle invalide pour « {self.identifiant_saisi} » "
                f"(probable faute de frappe)"
            )

        return None

    @property
    def identifiant_exploitable(self):
        """``True`` si on peut interroger l'API directement par identifiant."""
        return bool(self.identifiant) and self.motif_rejet is None

    @property
    def libelle(self):
        """Libellé court pour les logs et les messages d'erreur."""
        return self.nom or self.identifiant_saisi or f"prospect n°{self.rang}"

    def __repr__(self):
        return f"Prospect(rang={self.rang!r}, nom={self.nom!r})"


def _lire_json(chemin):
    """Lit le fichier et renvoie la liste brute des prospects.

    Args:
        chemin (str): chemin du fichier JSON.

    Returns:
        list: les entrées, telles qu'écrites dans le fichier.

    Raises:
        FichierProspectsInvalide: fichier absent, illisible, ou JSON mal formé.
    """
    try:
        with open(chemin, "r", encoding="utf-8") as fichier:
            donnees = json.load(fichier)
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

    # Tolérance : le tableau nu, ou emballé dans {"prospects": [...]}.
    if isinstance(donnees, dict):
        donnees = donnees.get("prospects")

    if not isinstance(donnees, list):
        raise FichierProspectsInvalide(
            f"{chemin} doit contenir un tableau de prospects "
            f"(ou un objet avec une clé « prospects »)."
        )

    return donnees


def charger_prospects(chemin):
    """Charge le fichier JSON de prospects et renvoie la liste des `Prospect`.

    Args:
        chemin (str): chemin du fichier JSON.

    Returns:
        list[Prospect]: les prospects exploitables, dans l'ordre du fichier.

    Raises:
        FichierProspectsInvalide: fichier absent, mal formé, ou sans aucun
            prospect exploitable.
    """
    entrees = _lire_json(chemin)

    prospects = []
    ignores = 0

    for rang, entree in enumerate(entrees, start=1):
        if not isinstance(entree, dict):
            logging.warning(f"Prospect n°{rang} ignoré : ce n'est pas un objet JSON")
            ignores += 1
            continue

        # Première clé d'identifiant renseignée : « siren », sinon « siret ».
        identifiant = next(
            (entree[cle] for cle in CLES_IDENTIFIANT if entree.get(cle)), ""
        )

        prospect = Prospect(
            rang=rang,
            nom=entree.get("nom"),
            identifiant_saisi=identifiant,
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
