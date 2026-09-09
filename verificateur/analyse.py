"""Transforme une réponse de l'API en verdict exploitable par le cabinet.

Deux questions : est-ce le bon prospect (exact par SIREN, approché par nom), et
est-il encore en activité. Le verdict tient dans `etat_activite` et `alerte`.
Aucun appel réseau ici, ce qui rend le module testable hors ligne.
"""

import logging
import re
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

# Part des mots du nom saisi qu'on doit retrouver pour valider une
# correspondance, réglée à la main sur notre jeu d'essai.
SEUIL_CORRESPONDANCE = 75

URL_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"

ETATS = {"A": ETAT_ACTIVE, "C": ETAT_CESSEE}

# « SARL Dupont » et « Dupont » désignent la même société.
FORMES_JURIDIQUES = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "EI", "EIRL",
    "STE", "SOCIETE", "GROUPE", "ETS", "ETABLISSEMENTS", "ET",
}

ACCENTS = {
    "À": "A", "Â": "A", "Ä": "A", "Ç": "C", "É": "E", "È": "E", "Ê": "E",
    "Ë": "E", "Î": "I", "Ï": "I", "Ô": "O", "Ö": "O", "Ù": "U", "Û": "U",
    "Ü": "U", "Ÿ": "Y",
}

MOTIF_MOT = r"[A-Z0-9]+"

# Champs du verdict, à blanc. Toute fiche les porte, même en échec, pour qu'un
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


def mots_significatifs(nom):
    """Réduit une dénomination à l'ensemble de ses mots comparables.

    >>> sorted(mots_significatifs("SARL Café de l'Étoile"))
    ['CAFE', 'DE', 'ETOILE', 'L']
    """
    texte = ""
    for caractere in str(nom or "").upper():
        texte = texte + ACCENTS.get(caractere, caractere)

    tous = re.findall(MOTIF_MOT, texte)

    mots = set()
    for mot in tous:
        if mot not in FORMES_JURIDIQUES:
            mots.add(mot)

    # Un nom fait *uniquement* de formes juridiques (« SARL ») ne doit pas
    # devenir un ensemble vide.
    if not mots:
        mots = set(tous)
    return mots


def score_ressemblance(nom_saisi, nom_officiel):
    """Part en % des mots du nom saisi retrouvés dans le nom officiel.

    Les mots en trop côté officiel ne pénalisent pas : l'API renvoie souvent
    « RAISON SOCIALE (SIGLE) ».
    """
    mots_saisis = mots_significatifs(nom_saisi)
    if not mots_saisis:
        return 0

    mots_officiels = mots_significatifs(nom_officiel)

    communs = 0
    for mot in mots_saisis:
        if mot in mots_officiels:
            communs += 1

    return int(100 * communs / len(mots_saisis))


def fiche_vide(prospect):
    """Fiche d'un prospect : rappel de l'entrée, verdict à blanc."""
    fiche = {
        "rang": prospect.rang,
        "nom_saisi": prospect.nom,
        "identifiant_saisi": prospect.identifiant_saisi,
        "contact": prospect.contact,
    }
    for champ, valeur in VERDICT_VIDE.items():
        fiche[champ] = valeur
    return fiche


def meilleur_candidat(nom, resultats):
    """Renvoie le résultat dont le nom ressemble le plus à `nom`."""
    meilleur = resultats[0]
    meilleur_score = score_ressemblance(nom, meilleur.get("nom_complet"))

    for entreprise in resultats[1:]:
        score = score_ressemblance(nom, entreprise.get("nom_complet"))
        if score > meilleur_score:
            meilleur = entreprise
            meilleur_score = score

    return meilleur


def _remplir_donnees(fiche, entreprise):
    """Recopie dans la fiche les informations officielles de l'entreprise."""
    # `.get()` partout : l'API omet des champs selon les entreprises, et un
    # KeyError ferait tomber le traitement pour une seule ligne.
    siege = entreprise.get("siege")
    if not siege:
        siege = {}

    fiche["nom_officiel"] = entreprise.get("nom_complet", "")
    fiche["siren"] = entreprise.get("siren", "")
    fiche["siret_siege"] = siege.get("siret", "")
    fiche["adresse_siege"] = siege.get("adresse", "")
    fiche["etat_activite"] = ETATS.get(entreprise.get("etat_administratif"), ETAT_INCONNU)

    cessation = entreprise.get("date_fermeture")
    if not cessation:
        cessation = siege.get("date_fermeture")
    fiche["date_cessation"] = cessation if cessation else ""

    if fiche["siren"]:
        fiche["url_annuaire"] = URL_ANNUAIRE + fiche["siren"]


def jours_depuis(chaine_date, aujourdhui):
    """Nombre de jours écoulés depuis une date ISO, ou ``None`` si illisible."""
    if not chaine_date:
        return None
    try:
        jour = datetime.fromisoformat(chaine_date).date()
    except ValueError:
        logging.debug(f"Date illisible ignorée : {chaine_date}")
        return None
    return (aujourdhui - jour).days


def qualifier(etat, date_cessation, aujourdhui):
    """Traduit un état d'activité en couple (alerte, message), du grave à l'anodin."""
    if etat == ETAT_CESSEE:
        jours = jours_depuis(date_cessation, aujourdhui)

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
    if aujourdhui is None:
        aujourdhui = date.today()

    fiche = fiche_vide(prospect)

    if not resultats:
        fiche["alerte"] = ALERTE_INTROUVABLE
        fiche["message"] = (
            f"Aucune entreprise pour « {prospect.libelle} » — non diffusible, "
            f"radiée avant informatisation, ou erreur de saisie."
        )
        return fiche

    if prospect.siren:
        # Recherche exacte : l'API ne peut renvoyer que cette entreprise.
        entreprise = resultats[0]
    else:
        entreprise = meilleur_candidat(prospect.nom, resultats)

    _remplir_donnees(fiche, entreprise)
    fiche["alerte"], fiche["message"] = qualifier(
        fiche["etat_activite"], fiche["date_cessation"], aujourdhui
    )

    # Sans nom saisi, il n'y a rien à comparer : le SIREN suffit à identifier.
    if prospect.nom:
        fiche["score_correspondance"] = score_ressemblance(
            prospect.nom, fiche["nom_officiel"]
        )
    else:
        fiche["score_correspondance"] = 100

    # Le nom ne colle pas : on demande confirmation au lieu d'affirmer. Couvre la
    # recherche par nom trop vague et le SIREN saisi avec le nom d'une autre.
    if fiche["score_correspondance"] < SEUIL_CORRESPONDANCE:
        if not fiche["alerte"]:
            fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE
        fiche["message"] = (
            f"Correspondance douteuse ({fiche['score_correspondance']} %) entre "
            f"« {prospect.nom} » et « {fiche['nom_officiel']} » — à confirmer. "
            + fiche["message"]
        )

    return fiche


def fiche_identifiant_invalide(prospect):
    """Fiche d'un prospect rejeté avant tout appel API."""
    fiche = fiche_vide(prospect)
    fiche["alerte"] = ALERTE_IDENTIFIANT_INVALIDE
    fiche["message"] = f"Identifiant non vérifiable : {prospect.motif_rejet}."
    return fiche


def fiche_erreur_api(prospect, erreur):
    """Fiche d'un prospect dont la vérification a échoué côté réseau.

    Une API en panne n'est pas une entreprise active : on la restitue comme
    « inconnu, à relancer » pour ne pas rendre un livrable trompeur.
    """
    fiche = fiche_vide(prospect)
    fiche["alerte"] = ALERTE_ERREUR_API
    fiche["message"] = f"Vérification impossible ({erreur}) — à relancer."
    return fiche


def compter(fiches):
    """Agrège les fiches en compteurs pour la synthèse et le rapport."""
    par_etat = {}
    par_alerte = {}
    a_signaler = 0

    for fiche in fiches:
        etat = fiche["etat_activite"]
        if etat in par_etat:
            par_etat[etat] += 1
        else:
            par_etat[etat] = 1

        alerte = fiche["alerte"]
        if alerte:
            a_signaler += 1
            if alerte in par_alerte:
                par_alerte[alerte] += 1
            else:
                par_alerte[alerte] = 1

    return {
        "total": len(fiches),
        "a_signaler": a_signaler,
        "par_etat": par_etat,
        "par_alerte": par_alerte,
    }
