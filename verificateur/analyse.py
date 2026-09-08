"""Métier : transformer une réponse d'API en verdict exploitable par le cabinet.

Trois responsabilités :

1. **Rapprocher** un prospect saisi à la main et une entreprise de la base.
   Par identifiant c'est exact ; par nom c'est approché, et il faut donc mesurer
   la ressemblance pour ne pas affirmer une correspondance douteuse.
2. **Traduire** les codes de l'INSEE en information lisible
   (`etat_administratif` valant ``"A"``/``"C"``, tranches d'effectif chiffrées…).
3. **Qualifier** le risque : entreprise cessée, cessation récente, état inconnu.

Ce module ne fait **aucun appel réseau** — ce qui le rend testable hors ligne
(cf. `tests/test_verificateur.py`).
"""

import difflib
import logging
import re
import unicodedata
from datetime import date, datetime

# --- Vocabulaire de sortie (valeurs stables, exploitables en aval) ------------

# statut_verification : qualité du rapprochement prospect ↔ base officielle
STATUT_VERIFIE = "VERIFIE"                    # correspondance sûre
STATUT_INCERTAIN = "INCERTAIN"                # trouvé par nom, ressemblance faible
STATUT_INTROUVABLE = "INTROUVABLE"            # aucun résultat dans la base
STATUT_IDENTIFIANT_INVALIDE = "IDENTIFIANT_INVALIDE"
STATUT_ERREUR_API = "ERREUR_API"              # on ne sait pas : l'appel a échoué

# etat_activite : le cœur de la demande du client
ETAT_ACTIVE = "ACTIVE"
ETAT_CESSEE = "CESSEE"
ETAT_INCONNU = "INCONNU"

# alerte : ce que le commercial doit voir avant de décrocher son téléphone
ALERTE_AUCUNE = ""
ALERTE_CESSATION_RECENTE = "CESSATION_RECENTE"
ALERTE_CESSEE = "CESSEE"
ALERTE_SIEGE_FERME = "SIEGE_FERME"
ALERTE_ETAT_INCONNU = "ETAT_INCONNU"
ALERTE_CORRESPONDANCE_INCERTAINE = "CORRESPONDANCE_INCERTAINE"
ALERTE_INTROUVABLE = "INTROUVABLE"
ALERTE_IDENTIFIANT_INVALIDE = "IDENTIFIANT_INVALIDE"
ALERTE_ERREUR_API = "ERREUR_API"

# Seuil de ressemblance (0-100) au-dessus duquel une correspondance par nom est
# considérée comme sûre. Valeur réglée à la main sur notre jeu d'essai.
SEUIL_CORRESPONDANCE = 75

URL_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"

# --- Traduction des codes INSEE ----------------------------------------------

# `etat_administratif` de l'unité légale
ETATS_UNITE_LEGALE = {"A": ETAT_ACTIVE, "C": ETAT_CESSEE}

# `etat_administratif` de l'établissement (le siège) : A = actif, F = fermé
ETATS_ETABLISSEMENT = {"A": "ACTIF", "F": "FERME"}

# Tranches d'effectif salarié de l'INSEE, rendues lisibles pour le livrable.
TRANCHES_EFFECTIF = {
    "NN": "non renseigné",
    "00": "0 salarié",
    "01": "1 ou 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    "11": "10 à 19 salariés",
    "12": "20 à 49 salariés",
    "21": "50 à 99 salariés",
    "22": "100 à 199 salariés",
    "31": "200 à 249 salariés",
    "32": "250 à 499 salariés",
    "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés",
    "51": "2 000 à 4 999 salariés",
    "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés et plus",
}

# Mots à retirer avant de comparer deux dénominations : formes juridiques et
# bruit de saisie. « SARL Dupont » et « Dupont » désignent la même société.
FORMES_JURIDIQUES = {
    "SARL", "SAS", "SASU", "EURL", "SA", "SCI", "SNC", "SCOP", "SELARL",
    "SCP", "GIE", "EI", "EIRL", "ASSOCIATION", "STE", "SOCIETE", "GROUPE",
    "ETS", "ETABLISSEMENTS", "CIE", "COMPAGNIE", "AND", "ET",
}

MOTIF_SEPARATEURS = re.compile(r"[^A-Z0-9]+")
# L'API renvoie souvent « NOM OFFICIEL (NOM COMMERCIAL) » : on isole les deux.
MOTIF_PARENTHESES = re.compile(r"\(([^)]*)\)")


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
        >>> normaliser("Boulangerie de l'Europe") == normaliser("BOULANGERIE DE L EUROPE")
        True
    """
    if not texte:
        return ""

    # NFD sépare les lettres de leurs accents ; on jette ensuite les accents.
    sans_accent = unicodedata.normalize("NFD", texte.upper())
    sans_accent = "".join(c for c in sans_accent if unicodedata.category(c) != "Mn")

    mots = [m for m in MOTIF_SEPARATEURS.split(sans_accent) if m]
    utiles = [m for m in mots if m not in FORMES_JURIDIQUES]

    # Si le nom n'était *que* des formes juridiques (ex. « SARL »), on garde
    # les mots d'origine plutôt que de renvoyer une chaîne vide.
    return " ".join(utiles or mots)


def score_ressemblance(nom_saisi, nom_officiel):
    """Note de 0 à 100 la ressemblance entre deux dénominations.

    L'API renvoie fréquemment « RAISON SOCIALE (NOM COMMERCIAL) » : on compare
    le nom saisi aux deux variantes et on garde la meilleure note.

    Args:
        nom_saisi (str): ce que le cabinet a tapé.
        nom_officiel (str): `nom_complet` renvoyé par l'API.

    Returns:
        int: 100 = identique après normalisation, 0 = aucun rapport.
    """
    reference = normaliser(nom_saisi)
    if not reference:
        return 0

    # Variantes : le libellé complet, et chaque morceau hors/dans parenthèses.
    variantes = [nom_officiel or ""]
    variantes += MOTIF_PARENTHESES.findall(nom_officiel or "")
    variantes.append(MOTIF_PARENTHESES.sub("", nom_officiel or ""))

    meilleur = 0
    for variante in variantes:
        candidat = normaliser(variante)
        if not candidat:
            continue
        ratio = difflib.SequenceMatcher(None, reference, candidat).ratio()
        meilleur = max(meilleur, round(ratio * 100))

    return meilleur


def _jours_depuis(chaine_date, aujourdhui):
    """Nombre de jours écoulés depuis une date ISO, ou ``None`` si illisible."""
    if not chaine_date:
        return None
    try:
        # Les dates de l'API sont en ISO : « 2025-09-19 » ou horodatées.
        jour = datetime.fromisoformat(chaine_date).date()
    except (ValueError, TypeError):
        logging.debug(f"Date illisible ignorée : {chaine_date!r}")
        return None
    return (aujourdhui - jour).days


def _premier_non_vide(*valeurs):
    """Renvoie la première valeur « utile » (ni None, ni chaîne vide)."""
    for valeur in valeurs:
        if valeur not in (None, "", []):
            return valeur
    return None


def fiche_vide(prospect):
    """Squelette de résultat : toutes les colonnes de sortie, à blanc.

    Garantit que **chaque ligne du CSV a exactement les mêmes colonnes**, même
    quand la vérification a échoué. C'est ce qui rend le livrable exploitable
    par un tableur ou un script en aval.
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
        "code_postal": "",
        "commune": "",
        "date_creation": "",
        "date_cessation": "",
        "jours_depuis_cessation": "",
        "cessation_recente": "",
        "etat_siege": "",
        "etablissements_ouverts": "",
        "tranche_effectif": "",
        "activite_principale": "",
        # Traçabilité
        "score_correspondance": "",
        "candidats_alternatifs": "",
        "fraicheur_donnee": "",
        "url_annuaire": "",
    }


def _remplir_donnees_officielles(fiche, entreprise, jours_recent, aujourdhui):
    """Recopie les champs de l'API dans la fiche et qualifie l'activité.

    Args:
        fiche (dict): fiche à compléter (modifiée sur place).
        entreprise (dict): un élément de `results` renvoyé par l'API.
        jours_recent (int): en-dessous de ce nombre de jours, une cessation est
            qualifiée de « récente ».
        aujourdhui (datetime.date): date de référence (injectée pour les tests).
    """
    # `.get()` partout : l'API omet des champs selon les entreprises, et un
    # KeyError ferait tomber tout le traitement pour une seule ligne.
    siege = entreprise.get("siege") or {}

    fiche["nom_officiel"] = entreprise.get("nom_complet") or ""
    fiche["siren"] = entreprise.get("siren") or ""
    fiche["siret_siege"] = siege.get("siret") or ""
    fiche["adresse_siege"] = siege.get("adresse") or ""
    fiche["code_postal"] = siege.get("code_postal") or ""
    fiche["commune"] = siege.get("libelle_commune") or ""
    fiche["date_creation"] = entreprise.get("date_creation") or ""
    fiche["activite_principale"] = entreprise.get("activite_principale") or ""

    code_effectif = entreprise.get("tranche_effectif_salarie")
    fiche["tranche_effectif"] = TRANCHES_EFFECTIF.get(
        code_effectif, code_effectif or ""
    )

    ouverts = entreprise.get("nombre_etablissements_ouverts")
    fiche["etablissements_ouverts"] = "" if ouverts is None else ouverts

    if fiche["siren"]:
        fiche["url_annuaire"] = URL_ANNUAIRE + fiche["siren"]

    etat_siege_brut = siege.get("etat_administratif")
    fiche["etat_siege"] = ETATS_ETABLISSEMENT.get(etat_siege_brut, "")

    # Fraîcheur de la donnée : depuis combien de temps la source n'a pas bougé.
    # Répond à la question du client « est-ce que mes infos sont à jour ? ».
    #
    # Attention à l'ordre : `date_mise_a_jour` est la date de réindexation de
    # l'API elle-même (souvent aujourd'hui), elle ne dit rien de l'âge réel de
    # l'information. On privilégie donc les dates des sources amont — INSEE puis
    # RNE — et on ne retombe sur celle de l'API qu'en dernier recours.
    maj = _premier_non_vide(
        entreprise.get("date_mise_a_jour_insee"),
        entreprise.get("date_mise_a_jour_rne"),
        entreprise.get("date_mise_a_jour"),
    )
    jours_maj = _jours_depuis(maj, aujourdhui)
    if jours_maj is not None:
        fiche["fraicheur_donnee"] = f"{jours_maj} j"

    # --- État d'activité ------------------------------------------------------
    etat_brut = entreprise.get("etat_administratif")
    fiche["etat_activite"] = ETATS_UNITE_LEGALE.get(etat_brut, ETAT_INCONNU)

    # Date de cessation : au niveau de l'unité légale, sinon celle du siège.
    # Cas réel rencontré : une société marquée « C » sans aucune date_fermeture
    # renseignée — on l'assume au lieu de faire planter le traitement.
    date_cessation = _premier_non_vide(
        entreprise.get("date_fermeture"), siege.get("date_fermeture")
    )
    fiche["date_cessation"] = date_cessation or ""

    jours = _jours_depuis(date_cessation, aujourdhui)
    if jours is not None:
        fiche["jours_depuis_cessation"] = jours
        fiche["cessation_recente"] = jours <= jours_recent

    # --- Alerte : du plus grave au plus anodin -------------------------------
    if fiche["etat_activite"] == ETAT_CESSEE:
        if fiche["cessation_recente"] is True:
            fiche["alerte"] = ALERTE_CESSATION_RECENTE
            fiche["message"] = (
                f"Cessation d'activité le {date_cessation} "
                f"(il y a {jours} jours) — ne pas démarcher."
            )
        elif date_cessation:
            fiche["alerte"] = ALERTE_CESSEE
            fiche["message"] = (
                f"Entreprise cessée depuis le {date_cessation} — ne pas démarcher."
            )
        else:
            fiche["alerte"] = ALERTE_CESSEE
            fiche["message"] = (
                "Entreprise déclarée cessée, date de cessation non renseignée "
                "dans la base — ne pas démarcher."
            )

    elif fiche["etat_activite"] == ETAT_INCONNU:
        fiche["alerte"] = ALERTE_ETAT_INCONNU
        fiche["message"] = (
            "État administratif absent de la base officielle "
            "— vérification manuelle nécessaire."
        )

    elif fiche["etat_siege"] == "FERME":
        # L'entreprise vit encore mais son siège est fermé : souvent un
        # déménagement, parfois le début d'un arrêt. À signaler sans dramatiser.
        fiche["alerte"] = ALERTE_SIEGE_FERME
        fiche["message"] = (
            "Entreprise active mais établissement siège fermé "
            "— adresse à confirmer."
        )

    elif ouverts == 0:
        fiche["alerte"] = ALERTE_SIEGE_FERME
        fiche["message"] = (
            "Entreprise active mais plus aucun établissement ouvert "
            "— à confirmer."
        )


def analyser_par_identifiant(prospect, resultats, jours_recent, aujourdhui=None):
    """Construit la fiche d'un prospect recherché par SIREN/SIRET.

    Args:
        prospect (Prospect): la ligne d'entrée.
        resultats (list[dict]): les `results` renvoyés par l'API.
        jours_recent (int): seuil de « cessation récente », en jours.
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

    entreprise = resultats[0]
    fiche["statut_verification"] = STATUT_VERIFIE
    # Recherche directe par identifiant : la correspondance est exacte.
    fiche["score_correspondance"] = 100
    _remplir_donnees_officielles(fiche, entreprise, jours_recent, aujourdhui)

    # Bonus : si le cabinet avait aussi noté un nom, on vérifie la cohérence.
    # Un SIREN juste avec un nom qui ne colle pas = ligne mal saisie.
    if prospect.nom and fiche["nom_officiel"]:
        score_nom = score_ressemblance(prospect.nom, fiche["nom_officiel"])
        if score_nom < SEUIL_CORRESPONDANCE:
            fiche["message"] = (
                f"Attention : le nom saisi « {prospect.nom} » ne correspond pas "
                f"à « {fiche['nom_officiel']} » pour ce SIREN. "
                + fiche["message"]
            ).strip()
            if fiche["alerte"] == ALERTE_AUCUNE:
                fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE

    if not fiche["message"]:
        fiche["message"] = "Entreprise active, informations à jour."

    return fiche


def analyser_par_nom(prospect, candidats, total, jours_recent, aujourdhui=None):
    """Construit la fiche d'un prospect recherché par dénomination.

    La recherche par nom est *approchée* : on ne peut pas se contenter du
    premier résultat. On note la ressemblance, on signale les homonymes, et on
    dégrade le statut en `INCERTAIN` plutôt que d'affirmer un faux positif.

    Args:
        prospect (Prospect): la ligne d'entrée.
        candidats (list[dict]): les premiers `results` renvoyés par l'API.
        total (int): nombre total de résultats annoncé par l'API.
        jours_recent (int): seuil de « cessation récente », en jours.
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

    # On classe les candidats par ressemblance, pas par pertinence API.
    notes = [
        (score_ressemblance(prospect.nom, c.get("nom_complet") or ""), c)
        for c in candidats
    ]
    notes.sort(key=lambda couple: couple[0], reverse=True)

    meilleur_score, entreprise = notes[0]
    fiche["score_correspondance"] = meilleur_score
    _remplir_donnees_officielles(fiche, entreprise, jours_recent, aujourdhui)

    # Les autres candidats sérieux : le cabinet doit pouvoir trancher à l'œil.
    autres = [
        f"{c.get('nom_complet', '?')} ({c.get('siren', '?')}, {score}%)"
        for score, c in notes[1:]
        if score >= SEUIL_CORRESPONDANCE
    ]
    fiche["candidats_alternatifs"] = " | ".join(autres)

    if meilleur_score < SEUIL_CORRESPONDANCE:
        fiche["statut_verification"] = STATUT_INCERTAIN
        fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE
        fiche["message"] = (
            f"Correspondance douteuse ({meilleur_score}%) entre "
            f"« {prospect.nom} » et « {fiche['nom_officiel']} » "
            f"sur {total} résultat(s) — à confirmer manuellement. "
            + fiche["message"]
        ).strip()
        return fiche

    fiche["statut_verification"] = STATUT_VERIFIE

    if autres:
        # Correspondance bonne, mais des homonymes existent : on prévient.
        if fiche["alerte"] == ALERTE_AUCUNE:
            fiche["alerte"] = ALERTE_CORRESPONDANCE_INCERTAINE
        fiche["message"] = (
            f"{len(autres) + 1} entreprises portent un nom proche "
            f"— vérifier qu'il s'agit du bon prospect. " + fiche["message"]
        ).strip()

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
    """Agrège les fiches en compteurs pour le rapport et le bloc `meta` du JSON.

    Args:
        fiches (list[dict]): les fiches produites.

    Returns:
        dict: compteurs par statut, par état d'activité et par alerte.
    """
    resume = {
        "total": len(fiches),
        "par_statut": {},
        "par_etat": {},
        "par_alerte": {},
        "a_signaler": 0,
    }

    for fiche in fiches:
        statut = fiche["statut_verification"]
        resume["par_statut"][statut] = resume["par_statut"].get(statut, 0) + 1

        etat = fiche["etat_activite"]
        resume["par_etat"][etat] = resume["par_etat"].get(etat, 0) + 1

        if fiche["alerte"]:
            alerte = fiche["alerte"]
            resume["par_alerte"][alerte] = resume["par_alerte"].get(alerte, 0) + 1

        if fiche["a_signaler"]:
            resume["a_signaler"] += 1

    return resume
