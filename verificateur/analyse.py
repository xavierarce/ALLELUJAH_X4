"""Métier : transformer une réponse de l'API en verdict pour le cabinet.

Deux questions, dans cet ordre :

1. **Est-ce le bon prospect ?** Par SIREN la recherche est exacte ; par nom elle
   est approchée, donc on mesure la ressemblance au lieu de croire l'API.
2. **Est-il encore en activité ?** Et si non, depuis quand.

Le verdict tient dans deux champs : `etat_activite` (ACTIVE / CESSEE / INCONNU)
et `alerte` (vide = rien à signaler). Ce module ne fait **aucun appel réseau**,
ce qui le rend testable hors ligne.
"""

import difflib
import re
import unicodedata
from collections import Counter
from datetime import date, datetime

# --- Vocabulaire de sortie (valeurs stables, exploitables en aval) ------------

ETAT_ACTIVE = "ACTIVE"
ETAT_CESSEE = "CESSEE"
ETAT_INCONNU = "INCONNU"

# `alerte` : ce que le commercial doit voir avant de décrocher son téléphone.
ALERTE_CESSATION_RECENTE = "CESSATION_RECENTE"
ALERTE_CESSEE = "CESSEE"
ALERTE_ETAT_INCONNU = "ETAT_INCONNU"
ALERTE_CORRESPONDANCE_INCERTAINE = "CORRESPONDANCE_INCERTAINE"
ALERTE_INTROUVABLE = "INTROUVABLE"
ALERTE_IDENTIFIANT_INVALIDE = "IDENTIFIANT_INVALIDE"
ALERTE_ERREUR_API = "ERREUR_API"

# Une cessation de moins d'un an est signalée comme « récente » : c'est le cas
# qui a piégé le cabinet (démarcher une société fermée depuis des mois).
JOURS_CESSATION_RECENTE = 365

# Ressemblance (0-100) au-dessus de laquelle un nom est considéré comme le bon.
# Valeur réglée à la main sur notre jeu d'essai.
SEUIL_CORRESPONDANCE = 75

URL_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"

# `etat_administratif` de l'unité légale, tel que renvoyé par l'API.
ETATS = {"A": ETAT_ACTIVE, "C": ETAT_CESSEE}

# Mots à retirer avant de comparer deux dénominations : « SARL Dupont » et
# « Dupont » désignent la même société.
FORMES_JURIDIQUES = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "EI", "EIRL",
    "STE", "SOCIETE", "GROUPE", "ETS", "ETABLISSEMENTS", "ET",
}

MOTIF_SEPARATEURS = re.compile(r"[^A-Z0-9]+")
# L'API renvoie souvent « RAISON SOCIALE (SIGLE) » : on compare aussi le nom
# saisi à la version sans la parenthèse.
MOTIF_PARENTHESES = re.compile(r"\([^)]*\)")

# Contrat du livrable : toute fiche porte exactement ces clés, même en échec.
# Un script en aval n'a donc jamais à tester la présence d'un champ.
VERDICT_VIDE = {
    "etat_activite": ETAT_INCONNU,
    "alerte": "",
    "message": "",
    "nom_officiel": "",
    "siren": "",
    "siret_siege": "",
    "adresse_siege": "",
    "date_cessation": "",
    "score_correspondance": "",
    "url_annuaire": "",
}


def normaliser(texte):
    """Réduit une dénomination à une forme comparable.

    Passe en majuscules, retire les accents et la ponctuation, puis supprime
    les formes juridiques.

    Args:
        texte (str): dénomination brute.

    Returns:
        str: forme normalisée, mots séparés par une espace simple.

    Examples:
        >>> normaliser("SARL Café de l'Étoile")
        'CAFE DE L ETOILE'
    """
    if not texte:
        return ""

    # NFD sépare les lettres de leurs accents ; on jette ensuite les accents.
    sans_accent = unicodedata.normalize("NFD", texte.upper())
    sans_accent = "".join(c for c in sans_accent if unicodedata.category(c) != "Mn")

    mots = [m for m in MOTIF_SEPARATEURS.split(sans_accent) if m]
    utiles = [m for m in mots if m not in FORMES_JURIDIQUES]

    # Si le nom n'était *que* des formes juridiques (ex. « SARL »), on garde les
    # mots d'origine plutôt que de renvoyer une chaîne vide.
    return " ".join(utiles or mots)


def score_ressemblance(nom_saisi, nom_officiel):
    """Note de 0 à 100 la ressemblance entre deux dénominations.

    Args:
        nom_saisi (str): ce que le cabinet a tapé.
        nom_officiel (str): `nom_complet` renvoyé par l'API.

    Returns:
        int: 100 = identique après normalisation, 0 = aucun rapport.
    """
    reference = normaliser(nom_saisi)
    if not reference:
        return 0

    meilleur = 0
    for variante in (nom_officiel, MOTIF_PARENTHESES.sub("", nom_officiel or "")):
        candidat = normaliser(variante)
        if candidat:
            ratio = difflib.SequenceMatcher(None, reference, candidat).ratio()
            meilleur = max(meilleur, round(ratio * 100))

    return meilleur


def _fiche(prospect, **verdict):
    """Assemble une fiche : rappel de l'entrée, puis le verdict.

    Les champs non fournis restent à blanc, ce qui garantit des fiches de même
    forme quelle que soit l'issue de la vérification.
    """
    return {
        "rang": prospect.rang,
        "nom_saisi": prospect.nom,
        "identifiant_saisi": prospect.identifiant_saisi,
        "contact": prospect.contact,
        **VERDICT_VIDE,
        **verdict,
    }


def _donnees_officielles(entreprise):
    """Extrait de la réponse API les informations à restituer au cabinet.

    `.get()` partout : l'API omet des champs selon les entreprises, et un
    KeyError ferait tomber le traitement pour une seule ligne.
    """
    siege = entreprise.get("siege") or {}
    siren = entreprise.get("siren") or ""
    return {
        "nom_officiel": entreprise.get("nom_complet") or "",
        "siren": siren,
        "siret_siege": siege.get("siret") or "",
        "adresse_siege": siege.get("adresse") or "",
        "etat_activite": ETATS.get(entreprise.get("etat_administratif"), ETAT_INCONNU),
        # Date de cessation : de l'unité légale, sinon celle du siège.
        "date_cessation": (
            entreprise.get("date_fermeture") or siege.get("date_fermeture") or ""
        ),
        "url_annuaire": URL_ANNUAIRE + siren if siren else "",
    }


def _jours_depuis(chaine_date, aujourdhui):
    """Nombre de jours écoulés depuis une date ISO, ou ``None`` si illisible."""
    if not chaine_date:
        return None
    try:
        return (aujourdhui - datetime.fromisoformat(chaine_date).date()).days
    except (ValueError, TypeError):
        return None


def _qualifier(etat, date_cessation, aujourdhui):
    """Traduit l'état d'activité en (alerte, message), du plus grave au plus anodin.

    Args:
        etat (str): `ETAT_ACTIVE`, `ETAT_CESSEE` ou `ETAT_INCONNU`.
        date_cessation (str): date ISO de cessation, ``""`` si absente.
        aujourdhui (datetime.date): date de référence.

    Returns:
        tuple[str, str]: l'alerte (vide si rien à signaler) et le message.
    """
    if etat == ETAT_CESSEE:
        jours = _jours_depuis(date_cessation, aujourdhui)
        if jours is not None and jours <= JOURS_CESSATION_RECENTE:
            return ALERTE_CESSATION_RECENTE, (
                f"Cessation d'activité le {date_cessation} (il y a {jours} jours) "
                f"— ne pas démarcher."
            )
        if date_cessation:
            return ALERTE_CESSEE, (
                f"Entreprise cessée depuis le {date_cessation} — ne pas démarcher."
            )
        # Cas réel rencontré : société marquée « C » sans aucune date de
        # fermeture. On l'assume au lieu de faire planter le traitement.
        return ALERTE_CESSEE, (
            "Entreprise déclarée cessée, date non renseignée dans la base "
            "— ne pas démarcher."
        )

    if etat == ETAT_INCONNU:
        return ALERTE_ETAT_INCONNU, (
            "État administratif absent de la base officielle "
            "— vérification manuelle nécessaire."
        )

    return "", "Entreprise active, informations à jour."


def analyser(prospect, resultats, aujourdhui=None):
    """Construit la fiche d'un prospect à partir des résultats de l'API.

    Args:
        prospect (entrees.Prospect): la ligne d'entrée.
        resultats (list[dict]): les `results` renvoyés par l'API.
        aujourdhui (datetime.date | None): date de référence (tests).

    Returns:
        dict: la fiche complète.
    """
    aujourdhui = aujourdhui or date.today()

    if not resultats:
        return _fiche(
            prospect,
            alerte=ALERTE_INTROUVABLE,
            message=(
                f"Aucune entreprise pour « {prospect.libelle} » — non diffusible, "
                f"radiée avant informatisation, ou erreur de saisie."
            ),
        )

    if prospect.siren:
        # Recherche exacte : l'API ne peut renvoyer que cette entreprise.
        entreprise = resultats[0]
    else:
        # Recherche par nom : on retient le plus ressemblant, pas le premier.
        entreprise = max(
            resultats,
            key=lambda e: score_ressemblance(prospect.nom, e.get("nom_complet") or ""),
        )

    donnees = _donnees_officielles(entreprise)
    alerte, message = _qualifier(
        donnees["etat_activite"], donnees["date_cessation"], aujourdhui
    )

    # Sans nom saisi, il n'y a rien à comparer : le SIREN suffit à identifier.
    score = (
        score_ressemblance(prospect.nom, donnees["nom_officiel"])
        if prospect.nom
        else 100
    )

    # Le nom saisi ne colle pas : on ne l'affirme pas, on demande confirmation.
    # Couvre les deux cas — recherche par nom trop vague, et SIREN saisi avec
    # le nom d'une autre société.
    if score < SEUIL_CORRESPONDANCE:
        alerte = alerte or ALERTE_CORRESPONDANCE_INCERTAINE
        message = (
            f"Correspondance douteuse ({score} %) entre « {prospect.nom} » et "
            f"« {donnees['nom_officiel']} » — à confirmer. " + message
        )

    return _fiche(
        prospect, score_correspondance=score, alerte=alerte, message=message, **donnees
    )


def fiche_identifiant_invalide(prospect):
    """Fiche d'une ligne rejetée avant tout appel API (identifiant incohérent)."""
    return _fiche(
        prospect,
        alerte=ALERTE_IDENTIFIANT_INVALIDE,
        message=f"Identifiant non vérifiable : {prospect.motif_rejet}.",
    )


def fiche_erreur_api(prospect, erreur):
    """Fiche d'une ligne dont la vérification a échoué côté réseau.

    Point d'honnêteté du livrable : une API en panne n'est **pas** une
    entreprise active. On la restitue comme « inconnu, à relancer ».
    """
    return _fiche(
        prospect,
        alerte=ALERTE_ERREUR_API,
        message=f"Vérification impossible ({erreur}) — à relancer.",
    )


def compter(fiches):
    """Agrège les fiches en compteurs, pour la synthèse console et le rapport.

    Args:
        fiches (list[dict]): les fiches produites.

    Returns:
        dict: total, répartition par état et par alerte, nombre à signaler.
    """
    alertes = [fiche["alerte"] for fiche in fiches if fiche["alerte"]]
    return {
        "total": len(fiches),
        "a_signaler": len(alertes),
        "par_etat": dict(Counter(fiche["etat_activite"] for fiche in fiches)),
        "par_alerte": dict(Counter(alertes)),
    }
