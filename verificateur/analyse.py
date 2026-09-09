"""Transforme une réponse de l'API en verdict exploitable par le cabinet.

Deux questions : est-ce le bon prospect (exact par SIREN, approché par nom), et
est-il encore en activité. Le verdict tient dans `etat_activite` et `alerte`.
Aucun appel réseau ici, ce qui rend le module testable hors ligne.
"""

import difflib
import re
import unicodedata
from collections import Counter
from datetime import date, datetime

ETAT_ACTIVE = "ACTIVE"
ETAT_CESSEE = "CESSEE"
ETAT_INCONNU = "INCONNU"

# Ce que le commercial doit voir avant de décrocher son téléphone.
ALERTE_CESSATION_RECENTE = "CESSATION_RECENTE"
ALERTE_CESSEE = "CESSEE"
ALERTE_ETAT_INCONNU = "ETAT_INCONNU"
ALERTE_CORRESPONDANCE_INCERTAINE = "CORRESPONDANCE_INCERTAINE"
ALERTE_INTROUVABLE = "INTROUVABLE"
ALERTE_IDENTIFIANT_INVALIDE = "IDENTIFIANT_INVALIDE"
ALERTE_ERREUR_API = "ERREUR_API"

# Le cas qui a piégé le cabinet : démarcher une société fermée depuis des mois.
JOURS_CESSATION_RECENTE = 365

# Ressemblance (0-100) au-dessus de laquelle un nom est considéré comme le bon,
# réglée à la main sur notre jeu d'essai.
SEUIL_CORRESPONDANCE = 75

URL_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"

ETATS = {"A": ETAT_ACTIVE, "C": ETAT_CESSEE}

# « SARL Dupont » et « Dupont » désignent la même société.
FORMES_JURIDIQUES = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "EI", "EIRL",
    "STE", "SOCIETE", "GROUPE", "ETS", "ETABLISSEMENTS", "ET",
}

MOTIF_SEPARATEURS = re.compile(r"[^A-Z0-9]+")
MOTIF_PARENTHESES = re.compile(r"\([^)]*\)")

# Contrat du livrable : toute fiche porte ces clés, même en échec, pour qu'un
# script en aval n'ait jamais à tester la présence d'un champ.
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

    >>> normaliser("SARL Café de l'Étoile")
    'CAFE DE L ETOILE'
    """
    if not texte:
        return ""

    # NFD sépare les lettres de leurs accents, qu'on jette ensuite.
    sans_accent = unicodedata.normalize("NFD", texte.upper())
    sans_accent = "".join(c for c in sans_accent if unicodedata.category(c) != "Mn")

    mots = [m for m in MOTIF_SEPARATEURS.split(sans_accent) if m]
    utiles = [m for m in mots if m not in FORMES_JURIDIQUES]

    # Un nom fait *uniquement* de formes juridiques (« SARL ») ne doit pas
    # devenir une chaîne vide.
    return " ".join(utiles or mots)


def score_ressemblance(nom_saisi, nom_officiel):
    """Note de 0 (aucun rapport) à 100 (identique) la ressemblance de deux noms."""
    reference = normaliser(nom_saisi)
    if not reference:
        return 0

    meilleur = 0
    # L'API renvoie souvent « RAISON SOCIALE (SIGLE) » : le sigle en trop ne
    # doit pas compter comme un écart.
    for variante in (nom_officiel, MOTIF_PARENTHESES.sub("", nom_officiel or "")):
        candidat = normaliser(variante)
        if candidat:
            ratio = difflib.SequenceMatcher(None, reference, candidat).ratio()
            meilleur = max(meilleur, round(ratio * 100))

    return meilleur


def _fiche(prospect, **verdict):
    """Assemble une fiche : rappel de l'entrée, puis le verdict."""
    return {
        "rang": prospect.rang,
        "nom_saisi": prospect.nom,
        "identifiant_saisi": prospect.identifiant_saisi,
        "contact": prospect.contact,
        **VERDICT_VIDE,
        **verdict,
    }


def _donnees_officielles(entreprise):
    """Extrait de la réponse API les informations à restituer au cabinet."""
    # `.get()` partout : l'API omet des champs selon les entreprises, et un
    # KeyError ferait tomber le traitement pour une seule ligne.
    siege = entreprise.get("siege") or {}
    siren = entreprise.get("siren") or ""
    return {
        "nom_officiel": entreprise.get("nom_complet") or "",
        "siren": siren,
        "siret_siege": siege.get("siret") or "",
        "adresse_siege": siege.get("adresse") or "",
        "etat_activite": ETATS.get(entreprise.get("etat_administratif"), ETAT_INCONNU),
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
    """Traduit un état d'activité en couple (alerte, message), du grave à l'anodin."""
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
        # Cas réel rencontré : société marquée « C » sans date de fermeture.
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
    """Construit la fiche d'un prospect à partir des résultats de l'API."""
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
        # Recherche par nom : le plus ressemblant, pas le premier de la liste.
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

    # Le nom ne colle pas : on demande confirmation au lieu d'affirmer. Couvre la
    # recherche par nom trop vague et le SIREN saisi avec le nom d'une autre.
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
    """Fiche d'un prospect rejeté avant tout appel API."""
    return _fiche(
        prospect,
        alerte=ALERTE_IDENTIFIANT_INVALIDE,
        message=f"Identifiant non vérifiable : {prospect.motif_rejet}.",
    )


def fiche_erreur_api(prospect, erreur):
    """Fiche d'un prospect dont la vérification a échoué côté réseau.

    Une API en panne n'est pas une entreprise active : on la restitue comme
    « inconnu, à relancer » pour ne pas rendre un livrable trompeur.
    """
    return _fiche(
        prospect,
        alerte=ALERTE_ERREUR_API,
        message=f"Vérification impossible ({erreur}) — à relancer.",
    )


def compter(fiches):
    """Agrège les fiches en compteurs pour la synthèse et le rapport."""
    alertes = [fiche["alerte"] for fiche in fiches if fiche["alerte"]]
    return {
        "total": len(fiches),
        "a_signaler": len(alertes),
        "par_etat": dict(Counter(fiche["etat_activite"] for fiche in fiches)),
        "par_alerte": dict(Counter(alertes)),
    }
