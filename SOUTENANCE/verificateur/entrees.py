"""Lecture et validation du fichier CSV de prospects (Séances 5 et 6).

Le cabinet nous fournit un CSV « tel quel » : les intitulés de colonnes varient
d'un export à l'autre, des lignes sont vides, des SIREN sont mal saisis. Ce
module absorbe ce désordre et ne laisse remonter que des objets `Prospect`
exploitables — ou un motif de rejet explicite.

Règles retenues :
  - on accepte plusieurs noms de colonnes (alias) pour le nom et l'identifiant ;
  - un identifiant est un SIREN (9 chiffres) ou un SIRET (14 chiffres), dont on
    vérifie la **clé de Luhn** pour rejeter les fautes de frappe sans dépenser
    un appel API ;
  - une ligne sans nom **et** sans identifiant est ignorée (avec un warning) ;
  - le fichier est lu en UTF-8 avec repli sur cp1252 (exports Excel français).
"""

import csv
import logging
import re

# Séance 5 : les motifs regex qui valident la *forme* de l'identifiant.
MOTIF_SIREN = re.compile(r"^\d{9}$")
MOTIF_SIRET = re.compile(r"^\d{14}$")
MOTIF_NON_CHIFFRE = re.compile(r"\D+")

# Intitulés de colonnes tolérés, en minuscules. Le premier trouvé gagne.
ALIAS_NOM = ("nom", "nom_entreprise", "entreprise", "raison_sociale", "societe")
ALIAS_IDENTIFIANT = ("siren", "siret", "identifiant", "id")
ALIAS_CONTACT = ("contact", "email", "mail", "commercial")

ENCODAGES = ("utf-8-sig", "cp1252")

# Séparateurs testés par le sniffer : Excel en configuration française exporte
# en point-virgule, les exports « standard » en virgule.
SEPARATEURS = ";,\t|"
SEPARATEUR_DEFAUT = ","


class FichierProspectsInvalide(Exception):
    """Le fichier d'entrée est inutilisable (absent, vide, sans colonne connue)."""


def cle_luhn_valide(numero):
    """Vérifie la clé de contrôle de Luhn d'un SIREN/SIRET.

    Le dernier chiffre d'un SIREN est une clé de contrôle : en doublant un
    chiffre sur deux (en partant de la droite, position paire), la somme de
    tous les chiffres obtenus doit être un multiple de 10.

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
    """Une ligne du fichier d'entrée, nettoyée et validée.

    Attributes:
        numero_ligne (int): numéro de la ligne dans le CSV (pour les messages).
        nom (str): nom de l'entreprise tel que saisi.
        contact (str): colonne libre reprise à l'identique dans la sortie.
        identifiant_saisi (str): identifiant tel que saisi (peut être vide).
        identifiant (str): identifiant réduit aux chiffres, ``""`` si absent.
        type_identifiant (str | None): ``"siren"``, ``"siret"`` ou ``None``.
        motif_rejet (str | None): renseigné si l'identifiant est inexploitable.
    """

    def __init__(self, numero_ligne, nom, identifiant_saisi="", contact=""):
        self.numero_ligne = numero_ligne
        self.nom = (nom or "").strip()
        self.contact = (contact or "").strip()
        self.identifiant_saisi = (identifiant_saisi or "").strip()
        self.identifiant = MOTIF_NON_CHIFFRE.sub("", self.identifiant_saisi)
        self.type_identifiant = None
        self.motif_rejet = None
        self._analyser_identifiant()

    def _analyser_identifiant(self):
        """Détermine le type de l'identifiant et sa validité."""
        if not self.identifiant:
            # Pas d'identifiant : on cherchera par nom, ce n'est pas une erreur.
            return

        if MOTIF_SIREN.match(self.identifiant):
            self.type_identifiant = "siren"
        elif MOTIF_SIRET.match(self.identifiant):
            self.type_identifiant = "siret"
        else:
            self.motif_rejet = (
                f"« {self.identifiant_saisi} » n'est ni un SIREN (9 chiffres) "
                f"ni un SIRET (14 chiffres)"
            )
            return

        # La forme est bonne : on contrôle la clé (les 9 premiers chiffres d'un
        # SIRET forment le SIREN, qui porte la clé de Luhn).
        if not cle_luhn_valide(self.identifiant[:9]):
            self.motif_rejet = (
                f"clé de contrôle invalide pour « {self.identifiant_saisi} » "
                f"(probable faute de frappe)"
            )

    @property
    def identifiant_exploitable(self):
        """``True`` si on peut interroger l'API directement par identifiant."""
        return self.type_identifiant is not None and self.motif_rejet is None

    @property
    def libelle(self):
        """Libellé court pour les logs et les messages d'erreur."""
        return self.nom or self.identifiant_saisi or f"ligne {self.numero_ligne}"

    def __repr__(self):
        return f"Prospect(ligne={self.numero_ligne!r}, nom={self.nom!r})"


def _colonne(entetes, alias):
    """Retrouve le nom réel d'une colonne parmi une liste d'alias.

    Args:
        entetes (list[str]): les en-têtes du CSV, tels que lus.
        alias (tuple[str]): les intitulés acceptés, en minuscules.

    Returns:
        str | None: l'en-tête réel correspondant, ou ``None`` si absent.
    """
    correspondance = {(e or "").strip().lower(): e for e in entetes}
    for nom_alias in alias:
        if nom_alias in correspondance:
            return correspondance[nom_alias]
    return None


def _detecter_separateur(echantillon):
    """Devine le séparateur du CSV à partir de ses premières lignes.

    Args:
        echantillon (str): début du fichier.

    Returns:
        str: le séparateur détecté, ou `SEPARATEUR_DEFAUT` en cas de doute.
    """
    try:
        separateur = csv.Sniffer().sniff(echantillon, delimiters=SEPARATEURS).delimiter
    except csv.Error:
        # Fichier à une seule colonne, ou trop irrégulier pour être deviné.
        logging.debug("Séparateur indétectable, repli sur la virgule")
        return SEPARATEUR_DEFAUT

    logging.debug(f"Séparateur détecté : {separateur!r}")
    return separateur


def _lire_lignes(chemin):
    """Lit le CSV et renvoie (en-têtes, lignes) en gérant encodage et séparateur.

    Raises:
        FichierProspectsInvalide: fichier absent, illisible ou vide.
    """
    derniere_erreur = None

    for encodage in ENCODAGES:
        try:
            with open(chemin, "r", encoding=encodage, newline="") as fichier:
                # On lit un échantillon pour deviner le séparateur, puis on
                # revient au début du fichier pour la lecture réelle.
                echantillon = fichier.read(4096)
                if not echantillon.strip():
                    raise FichierProspectsInvalide(f"{chemin} est vide.")
                fichier.seek(0)

                lecteur = csv.DictReader(
                    fichier, delimiter=_detecter_separateur(echantillon)
                )
                if lecteur.fieldnames is None:
                    raise FichierProspectsInvalide(f"{chemin} est vide.")
                # On matérialise la liste DANS le `with` : le fichier est encore
                # ouvert, et l'erreur d'encodage se déclenche ici.
                return lecteur.fieldnames, list(lecteur)
        except FileNotFoundError:
            # Inutile de tenter un autre encodage : le fichier n'existe pas.
            raise FichierProspectsInvalide(
                f"Fichier introuvable : {chemin}"
            ) from None
        except PermissionError:
            raise FichierProspectsInvalide(
                f"Accès refusé au fichier : {chemin}"
            ) from None
        except UnicodeDecodeError as erreur:
            logging.debug(f"Lecture en {encodage} impossible, on essaie le suivant")
            derniere_erreur = erreur

    raise FichierProspectsInvalide(
        f"Impossible de décoder {chemin} (encodages testés : "
        f"{', '.join(ENCODAGES)}) — détail : {derniere_erreur}"
    )


def charger_prospects(chemin):
    """Charge le CSV de prospects et renvoie la liste des `Prospect`.

    Args:
        chemin (str): chemin du fichier CSV.

    Returns:
        list[Prospect]: les lignes exploitables, dans l'ordre du fichier.

    Raises:
        FichierProspectsInvalide: si le fichier est absent, vide, ou si aucune
            colonne « nom » ni « siren » n'est reconnue.
    """
    entetes, lignes = _lire_lignes(chemin)
    logging.debug(f"Colonnes détectées : {entetes}")

    colonne_nom = _colonne(entetes, ALIAS_NOM)
    colonne_identifiant = _colonne(entetes, ALIAS_IDENTIFIANT)
    colonne_contact = _colonne(entetes, ALIAS_CONTACT)

    if colonne_nom is None and colonne_identifiant is None:
        raise FichierProspectsInvalide(
            f"{chemin} : aucune colonne exploitable. Il faut au moins une "
            f"colonne nommée parmi {ALIAS_NOM} ou {ALIAS_IDENTIFIANT}. "
            f"Colonnes trouvées : {entetes}"
        )

    prospects = []
    ignorees = 0

    # start=2 : la ligne 1 du fichier est l'en-tête.
    for numero_ligne, ligne in enumerate(lignes, start=2):
        prospect = Prospect(
            numero_ligne=numero_ligne,
            nom=ligne.get(colonne_nom, "") if colonne_nom else "",
            identifiant_saisi=(
                ligne.get(colonne_identifiant, "") if colonne_identifiant else ""
            ),
            contact=ligne.get(colonne_contact, "") if colonne_contact else "",
        )

        if not prospect.nom and not prospect.identifiant_saisi:
            logging.warning(f"Ligne {numero_ligne} ignorée : ni nom ni identifiant")
            ignorees += 1
            continue

        prospects.append(prospect)

    if not prospects:
        raise FichierProspectsInvalide(
            f"{chemin} ne contient aucun prospect exploitable "
            f"({ignorees} ligne(s) vide(s))."
        )

    logging.info(
        f"{len(prospects)} prospect(s) chargé(s) depuis {chemin}"
        + (f" — {ignorees} ligne(s) ignorée(s)" if ignorees else "")
    )
    return prospects
