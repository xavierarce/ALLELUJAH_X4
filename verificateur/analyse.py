"""Métier : transformer une réponse d'API en verdict exploitable par le cabinet.

Trois responsabilités :

1. **Rapprocher** un prospect saisi à la main et une entreprise de la base. Par
   identifiant c'est exact ; par nom c'est approché, et il faut donc mesurer la
   ressemblance pour ne pas affirmer une correspondance douteuse.
2. **Traduire** les codes de la base (`etat_administratif` valant ``"A"`` ou
   ``"C"``) en information lisible.
3. **Qualifier** le risque : cessée, cessation récente, état inconnu.

Ce module ne fait **aucun appel réseau** — ce qui le rend testable hors ligne
(cf. `tests/test_verificateur.py`).
"""

import difflib
import re
import unicodedata
from datetime import date, datetime

# --- Vocabulaire de sortie (valeurs stables, exploitables en aval) ------------

# statut_verification : qualité du rapprochement prospect ↔ base officielle
STATUT_VERIFIE = "VERIFIE"                          # correspondance sûre
STATUT_INCERTAIN = "INCERTAIN"                      # trouvé par nom, ressemblance faible
STATUT_INTROUVABLE = "INTROUVABLE"                  # aucun résultat dans la base
STATUT_IDENTIFIANT_INVALIDE = "IDENTIFIANT_INVALIDE"
STATUT_ERREUR_API = "ERREUR_API"                    # on ne sait pas : l'appel a échoué

# etat_activite : le cœur de la demande du client
ETAT_ACTIVE = "ACTIVE"
ETAT_CESSEE = "CESSEE"
ETAT_INCONNU = "INCONNU"

# alerte : ce que le commercial doit voir avant de décrocher son téléphone
ALERTE_AUCUNE = ""
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

# Ressemblance (0-100) au-dessus de laquelle une correspondance par nom est
# considérée comme sûre. Valeur réglée à la main sur notre jeu d'essai.
SEUIL_CORRESPONDANCE = 75

URL_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"

# `etat_administratif` de l'unité légale, tel que renvoyé par l'API.
ETATS = {"A": ETAT_ACTIVE, "C": ETAT_CESSEE}

# Mots à retirer avant de comparer deux dénominations : formes juridiques et
# bruit de saisie. « SARL Dupont » et « Dupont » désignent la même société.
FORMES_JURIDIQUES = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "EI", "EIRL",
    "STE", "SOCIETE", "GROUPE", "ETS", "ETABLISSEMENTS", "ET",
}

MOTIF_SEPARATEURS = re.compile(r"[^A-Z0-9]+")
# L'API renvoie souvent « RAISON SOCIALE (SIGLE) » : on compare aussi le nom
# saisi à la version sans la parenthèse.
MOTIF_PARENTHESES = re.compile(r"\([^)]*\)")


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


def _jours_depuis(chaine_date, aujourdhui):
    """Nombre de jours écoulés depuis une date ISO, ou ``None`` si illisible."""
    if not chaine_date:
        return None
    try:
        return (aujourdhui - datetime.fromisoformat(chaine_date).date()).days
    except (ValueError, TypeError):
        return None


def fiche_vide(prospect):
    """Squelette de résultat : toutes les colonnes de sortie, à blanc.

    Garantit que **chaque ligne du CSV a les mêmes colonnes**, même quand la
    vérification a échoué. C'est ce qui rend le livrable exploitable en aval.
    """
    return {
        # Rappel de l'entrée, pour que le cabinet retrouve ses lignes
        "numero_ligne": prospect.numero_ligne,
        "nom_saisi": prospect.nom,
        "identifiant_saisi": prospect.identifiant_saisi,
        "contact": prospect.contact,
        # Verdict
        "statut_verification": "",
        "etat_activite": ETAT_INCONNU,
        "alerte": ALERTE_AUCUNE,
        "a_signaler": False,
        "message": "",
        # Données officielles à jour
        "nom_officiel": "",
        "siren": "",
        "siret_siege": "",
        "adresse_siege": "",
        "date_creation": "",
        "date_cessation": "",
        # Traçabilité
        "score_correspondance": "",
        "url_annuaire": "",
    }


def _remplir(fiche, entreprise, aujourdhui):
    """Recopie les champs de l'API dans la fiche et qualifie l'activité.

    Args:
        fiche (dict): fiche à compléter (modifiée sur place).
        entreprise (dict): un élément de `results` renvoyé par l'API.
        aujourdhui (datetime.date): date de référence (injectée pour les tests).
    """
    # `.get()` partout : l'API omet des champs selon les entreprises, et un
    # KeyError ferait tomber tout le traitement pour une seule ligne.
    siege = entreprise.get("siege") or {}

    fiche["nom_officiel"] = entreprise.get("nom_complet") or ""
    fiche["siren"] = entreprise.get("siren") or ""
    fiche["siret_siege"] = siege.get("siret") or ""
    fiche["adresse_siege"] = siege.get("adresse") or ""
    fiche["date_creation"] = entreprise.get("date_creation") or ""

    if fiche["siren"]:
        fiche["url_annuaire"] = URL_ANNUAIRE + fiche["siren"]

    fiche["etat_activite"] = ETATS.get(entreprise.get("etat_administratif"), ETAT_INCONNU)

    # Date de cessation : au niveau de l'unité légale, sinon celle du siège.
    date_cessation = entreprise.get("date_fermeture") or siege.get("date_fermeture") or ""
    fiche["date_cessation"] = date_cessation
    jours = _jours_depuis(date_cessation, aujourdhui)

    # --- Alerte : du plus grave au plus anodin -------------------------------
    if fiche["etat_activite"] == ETAT_CESSEE:
        if jours is not None and jours <= JOURS_CESSATION_RECENTE:
            fiche["alerte"] = ALERTE_CESSATION_RECENTE
            fiche["message"] = (
                f"Cessation d'activité le {date_cessation} (il y a {jours} jours) "
                f"— ne pas démarcher."
            )
        elif date_cessation:
            fiche["alerte"] = ALERTE_CESSEE
            fiche["message"] = (
                f"Entreprise cessée depuis le {date_cessation} — ne pas démarcher."
            )
        else:
            # Cas réel rencontré : société marquée « C » sans aucune date de
            # fermeture. On l'assume au lieu de faire planter le traitement.
            fiche["alerte"] = ALERTE_CESSEE
            fiche["message"] = (
                "Entreprise déclarée cessée, date non renseignée dans la base "
                "— ne pas démarcher."
            )

    elif fiche["etat_activite"] == ETAT_INCONNU:
        fiche["alerte"] = ALERTE_ETAT_INCONNU
        fiche["message"] = (
            "État administratif absent de la base officielle "
            "— vérification manuelle nécessaire."
        )


def analyser_par_identifiant(prospect, resultats, aujourdhui=None):
    """Construit la fiche d'un prospect recherché par SIREN/SIRET.

    Args:
        prospect (entrees.Prospect): la ligne d'entrée.
        resultats (list[dict]): les `results` renvoyés par l'API.
        aujourdhui (datetime.date | None): date de référence (tests).

    Returns:
        dict: la fiche complète.
    """
    aujourdhui = aujourdhui or date.today()
    fiche = fiche_vide(prospect)

    if not resultats:
        fiche["statut_verification"] = STATUT_INTROUVABLE
        fiche["alerte"] = ALERTE_INTROUVABLE
        fiche["message"] = (
            f"Aucune entreprise pour l'identifiant {prospect.identifiant} "
            f"(non diffusible, radiée avant informatisation, ou erreur de saisie)."
        )
        return fiche

    fiche["statut_verification"] = STATUT_VERIFIE
    # Recherche directe par identifiant : la correspondance est exacte.
    fiche["score_correspondance"] = 100
    _remplir(fiche, resultats[0], aujourdhui)

    # Si le cabinet avait aussi noté un nom, on vérifie la cohérence : un SIREN
    # juste avec un nom qui ne colle pas, c'est une ligne mal saisie.
    if prospect.nom and fiche["nom_officiel"]:
        score = score_ressemblance(prospect.nom, fiche["nom_officiel"])
        if score < SEUIL_CORRESPONDANCE:
            fiche["message"] = (
                f"Attention : le nom saisi « {prospect.nom} » ne correspond pas à "
                f"« {fiche['nom_officiel']} » pour ce SIREN. " + fiche["message"]
            ).strip()
            if not fiche["alerte"]:
                fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE

    if not fiche["message"]:
        fiche["message"] = "Entreprise active, informations à jour."

    return fiche


def analyser_par_nom(prospect, candidats, aujourdhui=None):
    """Construit la fiche d'un prospect recherché par dénomination.

    La recherche par nom est *approchée* : on ne peut pas se contenter du
    premier résultat. On note la ressemblance de chaque candidat, et on dégrade
    le statut en `INCERTAIN` plutôt que d'affirmer un faux positif.

    Args:
        prospect (entrees.Prospect): la ligne d'entrée.
        candidats (list[dict]): les `results` renvoyés par l'API.
        aujourdhui (datetime.date | None): date de référence (tests).

    Returns:
        dict: la fiche complète.
    """
    aujourdhui = aujourdhui or date.today()
    fiche = fiche_vide(prospect)

    if not candidats:
        fiche["statut_verification"] = STATUT_INTROUVABLE
        fiche["alerte"] = ALERTE_INTROUVABLE
        fiche["message"] = (
            f"Aucune entreprise trouvée pour « {prospect.nom} » — "
            f"vérifier l'orthographe ou fournir le SIREN."
        )
        return fiche

    # On retient le candidat le plus ressemblant, pas le premier de la liste.
    score, entreprise = max(
        ((score_ressemblance(prospect.nom, c.get("nom_complet") or ""), c)
         for c in candidats),
        key=lambda couple: couple[0],
    )

    fiche["score_correspondance"] = score
    _remplir(fiche, entreprise, aujourdhui)

    if score < SEUIL_CORRESPONDANCE:
        fiche["statut_verification"] = STATUT_INCERTAIN
        fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE
        fiche["message"] = (
            f"Correspondance douteuse ({score} %) entre « {prospect.nom} » et "
            f"« {fiche['nom_officiel']} » — à confirmer manuellement. "
            + fiche["message"]
        ).strip()
        return fiche

    fiche["statut_verification"] = STATUT_VERIFIE
    if not fiche["message"]:
        fiche["message"] = "Entreprise active, informations à jour."

    return fiche


def fiche_identifiant_invalide(prospect):
    """Fiche d'une ligne rejetée avant tout appel API (identifiant incohérent)."""
    fiche = fiche_vide(prospect)
    fiche["statut_verification"] = STATUT_IDENTIFIANT_INVALIDE
    fiche["alerte"] = ALERTE_IDENTIFIANT_INVALIDE
    fiche["message"] = f"Identifiant non vérifiable : {prospect.motif_rejet}."
    return fiche


def fiche_erreur_api(prospect, message):
    """Fiche d'une ligne dont la vérification a échoué côté réseau.

    Important pour l'honnêteté du livrable : une erreur d'API n'est **pas** une
    entreprise active. On l'affiche comme « inconnu, à relancer ».
    """
    fiche = fiche_vide(prospect)
    fiche["statut_verification"] = STATUT_ERREUR_API
    fiche["alerte"] = ALERTE_ERREUR_API
    fiche["message"] = f"Vérification impossible ({message}) — à relancer."
    return fiche


def marquer_a_signaler(fiche):
    """Renseigne `a_signaler` : la fiche doit-elle remonter dans les alertes ?

    Tout ce qui n'est pas « entreprise active et correspondance sûre » mérite un
    œil humain. C'est le « signalement clair » demandé par le client.
    """
    fiche["a_signaler"] = bool(fiche["alerte"])
    return fiche


def compter(fiches):
    """Agrège les fiches en compteurs, pour le rapport console et le JSON.

    Args:
        fiches (list[dict]): les fiches produites.

    Returns:
        dict: total, répartition par état et par alerte, nombre à signaler.
    """
    resume = {
        "total": len(fiches),
        "par_etat": {},
        "par_alerte": {},
        "a_signaler": 0,
    }

    for fiche in fiches:
        etat = fiche["etat_activite"]
        resume["par_etat"][etat] = resume["par_etat"].get(etat, 0) + 1

        if fiche["alerte"]:
            resume["par_alerte"][fiche["alerte"]] = (
                resume["par_alerte"].get(fiche["alerte"], 0) + 1
            )
        if fiche["a_signaler"]:
            resume["a_signaler"] += 1

    return resume
