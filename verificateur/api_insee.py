"""Client de l'API Sirene de l'INSEE — **source alternative, nécessite une clé**.

Documentation : https://portail-api.insee.fr — API Sirene 3.11
Base : `https://api.insee.fr/api-sirene/3.11`

Pourquoi ce second client
-------------------------
`api_entreprises.py` interroge l'API Recherche d'entreprises (ouverte, sans
clé) : c'est la source **par défaut**, celle qui permet de lancer le projet sur
n'importe quelle machine. L'API Sirene est la base de référence de l'INSEE :
plus complète et **historisée** (on connaît chaque changement d'état avec sa
date), mais elle impose une clé et un quota beaucoup plus serré.

Ce module est un **adaptateur** : il traduit les réponses de l'INSEE dans le
même format de dictionnaire que `api_entreprises`. Conséquence : `analyse.py`,
`sorties.py` et les tests existants n'ont pas eu à changer d'une ligne pour
accepter une deuxième source.

Authentification
----------------
La clé se transmet dans l'en-tête HTTP `X-INSEE-Api-Key-Integration` (ni
`Authorization: Bearer`, ni paramètre d'URL — les deux renvoient 401).

Elle est lue dans la variable d'environnement `INSEE_API_KEY` et **jamais
écrite dans le code ni dans un fichier du dépôt** :

    export INSEE_API_KEY='votre-cle'
    python3 verif_prospects.py donnees/prospects.csv --source insee

Quotas (relevés dans les en-têtes de réponse `X-Rate-Limit-*`)
--------------------------------------------------------------
  - **30 requêtes par minute** — soit 0,5 req/s, contre 7 req/s pour l'API
    ouverte : c'est 14 fois plus serré, et ça change complètement la stratégie
    de parallélisation (cf. `Limiteur` ci-dessous) ;
  - **2 000 requêtes par jour** (`X-Quota-Limit`).
"""

import logging
import os
import re
import threading
import time

import requests

# Les deux clients partagent le même contrat d'erreur : l'orchestrateur n'a
# qu'un seul type d'exception à attraper, quelle que soit la source.
from .api_entreprises import ErreurAPI

URL_BASE = "https://api.insee.fr/api-sirene/3.11"
VARIABLE_CLE = "INSEE_API_KEY"
ENTETE_CLE = "X-INSEE-Api-Key-Integration"
USER_AGENT = "verif-prospects-esgi/1.0 (projet pedagogique ESGI - Python)"

DELAI_TIMEOUT = 20          # l'INSEE est sensiblement plus lente que l'API ouverte
TENTATIVES_MAX = 3
ATTENTE_BASE = 2.0
ATTENTE_MAX = 65.0          # un quota par minute peut imposer d'attendre ~1 min

CODES_A_REESSAYER = (429, 500, 502, 503, 504)

# Quota officiel : 30 requêtes par minute. On se cale volontairement en dessous
# pour absorber les imprécisions d'horloge et les tentatives supplémentaires.
REQUETES_PAR_FENETRE = 28
FENETRE_SECONDES = 60.0

# Au-delà de ce nombre de threads, le limiteur ne fait plus qu'attendre : le
# parallélisme ne sert à rien quand la source plafonne à 0,5 requête/seconde.
WORKERS_RECOMMANDES = 2


class CleInseeManquante(Exception):
    """La variable d'environnement contenant la clé API n'est pas définie."""


def cle_api():
    """Lit la clé API dans l'environnement.

    Returns:
        str: la clé.

    Raises:
        CleInseeManquante: si la variable d'environnement est absente ou vide.
    """
    cle = (os.environ.get(VARIABLE_CLE) or "").strip()
    if not cle:
        raise CleInseeManquante(
            f"La source « insee » nécessite une clé API. "
            f"Définissez-la dans l'environnement :\n"
            f"    export {VARIABLE_CLE}='votre-cle'\n"
            f"Obtention (gratuite) : https://portail-api.insee.fr"
        )
    return cle


class Limiteur:
    """Limiteur de débit à fenêtre glissante, partagé par tous les threads.

    L'INSEE plafonne à 30 requêtes/minute. Sans limiteur, un pool de threads
    déclenche des HTTP 429 en rafale. On mémorise donc l'horodatage des appels
    récents et, si la fenêtre est pleine, on attend que le plus ancien en sorte.

    C'est l'inverse de la stratégie retenue pour l'API ouverte : là-bas on
    parallélise à 5 threads, ici on **freine volontairement**.

    Compteur local **et** avis du serveur
    -------------------------------------
    Le compteur interne ne connaît que les appels de *ce* processus. Deux
    exécutions lancées à une minute d'intervalle repartent donc chacune d'un
    compteur vide, alors que le quota est commun côté INSEE : c'est exactement
    comme ça qu'on a déclenché une rafale de 429 en test.

    La réponse porte heureusement l'état réel du quota
    (`X-Rate-Limit-Remaining`, `X-Rate-Limit-Reset`). On s'y aligne via
    `enregistrer_reponse()` : quand le serveur annonce qu'il ne reste plus rien,
    on se met en pause jusqu'à la réinitialisation de la fenêtre, sans attendre
    d'avoir pris un 429.
    """

    def __init__(self, requetes=REQUETES_PAR_FENETRE, fenetre=FENETRE_SECONDES):
        self.requetes = requetes
        self.fenetre = fenetre
        self._appels = []                 # horodatages des appels récents
        self._verrou = threading.Lock()   # protège l'état entre threads
        self._reprise_apres = 0.0         # `time.monotonic()` de fin de pause

    def attendre_son_tour(self):
        """Bloque le thread appelant jusqu'à ce qu'un appel soit autorisé."""
        while True:
            with self._verrou:
                maintenant = time.monotonic()

                # 1. Pause imposée par le serveur (quota épuisé ou 429 reçu).
                if maintenant < self._reprise_apres:
                    attente = self._reprise_apres - maintenant

                else:
                    # 2. Compteur local : on oublie les appels sortis de la fenêtre.
                    self._appels = [
                        t for t in self._appels if maintenant - t < self.fenetre
                    ]

                    if len(self._appels) < self.requetes:
                        self._appels.append(maintenant)
                        return

                    attente = self.fenetre - (maintenant - self._appels[0]) + 0.05

            # On dort TOUJOURS hors du verrou, sinon on bloquerait les autres
            # threads pendant toute la pause.
            logging.debug(f"Quota INSEE atteint — pause de {attente:.1f}s")
            time.sleep(min(attente, ATTENTE_MAX))

    def imposer_pause(self, secondes):
        """Interdit tout nouvel appel pendant `secondes`.

        Appelée quand le serveur a répondu 429 : inutile que les autres threads
        aillent se cogner au même mur.
        """
        if secondes <= 0:
            return
        with self._verrou:
            self._reprise_apres = max(
                self._reprise_apres, time.monotonic() + secondes
            )

    def enregistrer_reponse(self, entetes):
        """Aligne le limiteur sur l'état de quota annoncé par le serveur.

        Args:
            entetes (Mapping): les en-têtes de la réponse HTTP.
        """
        reste = entetes.get("X-Rate-Limit-Remaining")
        reset = entetes.get("X-Rate-Limit-Reset")
        if reste is None:
            return

        try:
            reste = int(reste)
        except (TypeError, ValueError):
            return

        if reste > 0:
            return

        # Plus rien dans la fenêtre courante : on attend sa réinitialisation.
        attente = secondes_jusqu_au_reset(reset)
        logging.info(
            f"Quota INSEE épuisé pour cette fenêtre — pause de {attente:.0f}s"
        )
        self.imposer_pause(attente)


def secondes_jusqu_au_reset(entete_reset, maintenant=None):
    """Convertit l'en-tête `X-Rate-Limit-Reset` en un nombre de secondes.

    L'INSEE renvoie un horodatage **epoch en millisecondes** (par exemple
    ``1788876316175``). On le compare à l'heure courante pour savoir combien de
    temps attendre, en se rabattant sur la durée de la fenêtre si l'en-tête est
    absent ou illisible.

    Args:
        entete_reset (str | None): la valeur de l'en-tête.
        maintenant (float | None): heure courante epoch en secondes (tests).

    Returns:
        float: secondes à attendre, bornées à `ATTENTE_MAX`.
    """
    maintenant = time.time() if maintenant is None else maintenant

    try:
        reset_secondes = float(entete_reset) / 1000.0
    except (TypeError, ValueError):
        return FENETRE_SECONDES

    attente = reset_secondes - maintenant

    # Un en-tête absurde (déjà passé, ou très loin) ne doit pas bloquer l'outil.
    if attente <= 0:
        return 0.0
    return min(attente, ATTENTE_MAX)


# Limiteur unique pour tout le processus.
_limiteur = Limiteur()

# Une session par thread (cf. `api_entreprises` : Session n'est pas thread-safe).
_local = threading.local()


def _session():
    """Renvoie la `Session` requests propre au thread courant."""
    if not hasattr(_local, "session"):
        session = requests.Session()
        session.headers.update(
            {
                ENTETE_CLE: cle_api(),
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        _local.session = session
    return _local.session


def _duree_attente(reponse, tentative):
    """Temps d'attente avant nouvelle tentative.

    Ordre de préférence :

    1. `Retry-After`, quand le serveur le fournit ;
    2. `X-Rate-Limit-Reset` sur un 429 — indispensable ici : le quota est
       **par minute**, donc un back-off de 2 puis 4 secondes épuise les trois
       tentatives en 6 secondes et échoue à coup sûr. Il faut attendre la
       réinitialisation de la fenêtre, pas quelques secondes ;
    3. back-off exponentiel, pour les pannes qui ne sont pas des quotas.
    """
    if reponse is not None:
        entete = reponse.headers.get("Retry-After")
        if entete:
            try:
                return min(float(entete), ATTENTE_MAX)
            except ValueError:
                logging.debug(f"Retry-After illisible : {entete!r}")

        if reponse.status_code == 429:
            return secondes_jusqu_au_reset(
                reponse.headers.get("X-Rate-Limit-Reset")
            )

    return min(ATTENTE_BASE * (2 ** (tentative - 1)), ATTENTE_MAX)


def _appeler(chemin, parametres=None):
    """Appelle un endpoint de l'API Sirene et renvoie le JSON décodé.

    Args:
        chemin (str): chemin relatif, par exemple ``"/siret"``.
        parametres (dict | None): paramètres de requête.

    Returns:
        dict: le corps JSON de la réponse.

    Raises:
        ErreurAPI: après épuisement des tentatives, ou sur erreur définitive.
        CleInseeManquante: si la clé n'est pas configurée.
    """
    url = URL_BASE + chemin
    derniere_erreur = "erreur inconnue"

    for tentative in range(1, TENTATIVES_MAX + 1):
        reponse = None

        # On demande son tour au limiteur AVANT chaque tentative : une nouvelle
        # tentative consomme du quota elle aussi.
        _limiteur.attendre_son_tour()

        try:
            logging.debug(f"GET {url} {parametres} (essai {tentative})")
            reponse = _session().get(url, params=parametres, timeout=DELAI_TIMEOUT)

        except requests.Timeout:
            derniere_erreur = f"délai dépassé (> {DELAI_TIMEOUT}s)"
        except requests.ConnectionError:
            derniere_erreur = "connexion impossible (réseau ou DNS)"
        except requests.RequestException as erreur:
            derniere_erreur = f"erreur requests : {erreur}"

        else:
            # On s'aligne sur l'état de quota annoncé par le serveur, quel que
            # soit le code de retour : c'est la seule information fiable, le
            # compteur local ignorant les appels des autres exécutions.
            _limiteur.enregistrer_reponse(reponse.headers)

            if reponse.status_code == 200:
                try:
                    return reponse.json()
                except ValueError:
                    raise ErreurAPI(
                        "réponse illisible : l'INSEE n'a pas renvoyé du JSON"
                    ) from None

            # 404 = SIREN absent de Sirene. Ce n'est pas une panne : c'est une
            # réponse métier, qu'on traduit en « aucun résultat ».
            if reponse.status_code == 404:
                return {}

            if reponse.status_code == 401:
                raise ErreurAPI(
                    f"HTTP 401 — clé API refusée par l'INSEE "
                    f"(vérifiez ${VARIABLE_CLE})"
                )

            if reponse.status_code == 403:
                raise ErreurAPI(
                    "HTTP 403 — accès refusé : la clé n'a peut-être pas "
                    "souscrit à l'API Sirene"
                )

            if reponse.status_code in CODES_A_REESSAYER:
                derniere_erreur = f"HTTP {reponse.status_code}"
                if reponse.status_code == 429:
                    derniere_erreur += " (quota de 30 requêtes/min dépassé)"
            else:
                detail = ""
                try:
                    detail = reponse.json().get("header", {}).get("message", "")
                except ValueError:
                    detail = ""
                raise ErreurAPI(
                    f"HTTP {reponse.status_code}" + (f" — {detail}" if detail else "")
                )

        if tentative < TENTATIVES_MAX:
            attente = _duree_attente(reponse, tentative)

            # Un 429 concerne tout le processus : on met les autres threads en
            # pause aussi, au lieu de les laisser prendre le même mur.
            if reponse is not None and reponse.status_code == 429:
                _limiteur.imposer_pause(attente)

            logging.warning(
                f"INSEE : {derniere_erreur} — nouvelle tentative dans "
                f"{attente:.1f}s ({tentative}/{TENTATIVES_MAX - 1})"
            )
            time.sleep(attente)

    raise ErreurAPI(f"{derniere_erreur} après {TENTATIVES_MAX} tentatives")


# --- Traduction INSEE → format commun ----------------------------------------

# Caractères réservés par la syntaxe de recherche : on les retire du nom plutôt
# que de les échapper un par un, sinon l'API répond « erreur de syntaxe ».
MOTIF_CARACTERES_RESERVES = re.compile(r'["\\+\-!(){}\[\]^~*?:/]|&&|\|\|')


def _assainir_nom(nom):
    """Neutralise les caractères réservés de la syntaxe de recherche INSEE."""
    return MOTIF_CARACTERES_RESERVES.sub(" ", nom or "").strip()


def _construire_adresse(adresse):
    """Recompose une adresse sur une ligne à partir des champs INSEE.

    L'API ouverte fournit une adresse déjà assemblée ; l'INSEE la livre
    éclatée en une quinzaine de champs. On reconstitue la même forme pour que
    les deux sources produisent des livrables comparables.

    Args:
        adresse (dict): le bloc ``adresseEtablissement``.

    Returns:
        str: par exemple ``"111 QUAI DU PRESIDENT ROOSEVELT 92130 ISSY-LES-MOULINEAUX"``.
    """
    if not adresse:
        return ""

    morceaux = [
        adresse.get("numeroVoieEtablissement"),
        adresse.get("indiceRepetitionEtablissement"),
        adresse.get("typeVoieEtablissement"),
        adresse.get("libelleVoieEtablissement"),
        adresse.get("codePostalEtablissement"),
        adresse.get("libelleCommuneEtablissement")
        or adresse.get("libelleCommuneEtrangerEtablissement"),
    ]
    return " ".join(m for m in morceaux if m)


def _denomination(unite_legale):
    """Construit le nom affichable d'une unité légale.

    Une unité légale est soit une société (`denominationUniteLegale`), soit une
    personne physique (entrepreneur individuel), auquel cas il faut recomposer
    le nom à partir du prénom et du nom de famille.
    """
    denomination = unite_legale.get("denominationUniteLegale")

    if not denomination:
        # Personne physique : « PRENOM NOM ».
        parties = [
            unite_legale.get("prenomUsuelUniteLegale")
            or unite_legale.get("prenom1UniteLegale"),
            unite_legale.get("nomUsageUniteLegale")
            or unite_legale.get("nomUniteLegale"),
        ]
        denomination = " ".join(p for p in parties if p)

    sigle = unite_legale.get("sigleUniteLegale")
    if sigle and sigle != denomination:
        # Même convention que l'API ouverte : « NOM OFFICIEL (SIGLE) ».
        denomination = f"{denomination} ({sigle})".strip()

    return denomination or ""


def _date_cessation(siren):
    """Récupère la date exacte de cessation d'une unité légale.

    Coûte **un appel supplémentaire**, donc on ne l'effectue que pour les
    entreprises effectivement cessées — une minorité des prospects.

    Piège vérifié sur des cas réels : `periodesUniteLegale[0].dateDebut` n'est
    une date de cessation que si cette période marque bien un *changement*
    d'état. Une unité légale créée puis cessée sans transition enregistrée n'a
    qu'une seule période, dont `dateDebut` vaut la date de **création** — la
    prendre pour une date de cessation serait un contresens (cas réel : SIREN
    923804504, créée et cessée le 2023-07-20 selon la seule période existante).

    Args:
        siren (str): le SIREN de l'unité légale.

    Returns:
        str | None: la date ISO de cessation, ou ``None`` si indéterminable.
    """
    try:
        donnees = _appeler(f"/siren/{siren}")
    except ErreurAPI as erreur:
        # On ne fait pas échouer toute la fiche pour une date manquante.
        logging.warning(f"Date de cessation indisponible pour {siren} : {erreur}")
        return None

    periodes = (donnees.get("uniteLegale") or {}).get("periodesUniteLegale") or []
    if not periodes:
        return None

    courante = periodes[0]
    if courante.get("etatAdministratifUniteLegale") != "C":
        return None

    # Seul un changement d'état daté fait une vraie date de cessation.
    if not courante.get("changementEtatAdministratifUniteLegale"):
        logging.debug(
            f"{siren} : cessée sans changement d'état daté — date inconnue"
        )
        return None

    return courante.get("dateDebut")


def _convertir(etablissement):
    """Traduit un établissement INSEE dans le format de `api_entreprises`.

    C'est le cœur de l'adaptateur : en sortie, `analyse.py` ne peut pas
    distinguer une fiche venant de l'INSEE d'une fiche venant de l'API ouverte.

    Args:
        etablissement (dict): un élément de ``etablissements``.

    Returns:
        dict: la même structure que celle renvoyée par l'API Recherche
            d'entreprises (clés ``siren``, ``nom_complet``,
            ``etat_administratif``, ``siege``, …).
    """
    unite = etablissement.get("uniteLegale") or {}
    adresse = etablissement.get("adresseEtablissement") or {}
    periodes = etablissement.get("periodesEtablissement") or [{}]
    periode = periodes[0]

    etat_unite = unite.get("etatAdministratifUniteLegale")
    etat_etablissement = periode.get("etatAdministratifEtablissement")

    # Date de fermeture de l'établissement : même précaution que pour l'unité
    # légale — il faut un changement d'état daté.
    date_fermeture_siege = None
    if etat_etablissement == "F" and periode.get(
        "changementEtatAdministratifEtablissement"
    ):
        date_fermeture_siege = periode.get("dateDebut")

    # Date de cessation de l'unité légale : appel supplémentaire, seulement si
    # l'entreprise est cessée.
    date_cessation = None
    if etat_unite == "C":
        date_cessation = _date_cessation(etablissement.get("siren", ""))

    return {
        "siren": etablissement.get("siren") or "",
        "nom_complet": _denomination(unite),
        "nom_raison_sociale": unite.get("denominationUniteLegale") or "",
        "etat_administratif": etat_unite,
        "date_creation": unite.get("dateCreationUniteLegale") or "",
        "date_fermeture": date_cessation,
        "activite_principale": unite.get("activitePrincipaleUniteLegale") or "",
        # Les codes de tranche d'effectif sont ceux de l'INSEE dans les deux
        # sources : le dictionnaire de traduction de `analyse.py` marche tel quel.
        "tranche_effectif_salarie": unite.get("trancheEffectifsUniteLegale"),
        "categorie_entreprise": unite.get("categorieEntreprise"),
        # Non disponible en un seul appel côté INSEE : laissé vide plutôt que
        # deviné. `analyse.py` sait gérer l'absence.
        "nombre_etablissements_ouverts": None,
        "date_mise_a_jour_insee": unite.get("dateDernierTraitementUniteLegale"),
        "siege": {
            "siret": etablissement.get("siret") or "",
            "adresse": _construire_adresse(adresse),
            "code_postal": adresse.get("codePostalEtablissement") or "",
            "libelle_commune": adresse.get("libelleCommuneEtablissement") or "",
            "etat_administratif": etat_etablissement,
            "date_fermeture": date_fermeture_siege,
            "date_creation": etablissement.get("dateCreationEtablissement") or "",
        },
    }


# --- Interface publique, identique à celle de `api_entreprises` ---------------


def chercher_par_identifiant(identifiant):
    """Recherche par SIREN (9 chiffres) ou SIRET (14 chiffres).

    Un SIRET est ramené à son SIREN : comme l'API ouverte, on renvoie l'unité
    légale accompagnée de son **établissement siège**, pas l'établissement
    quelconque qui aurait été saisi.

    Args:
        identifiant (str): SIREN ou SIRET, chiffres uniquement.

    Returns:
        list[dict]: 0 ou 1 entreprise, au format commun.

    Raises:
        ErreurAPI: en cas d'échec de l'appel.
    """
    siren = identifiant[:9]
    donnees = _appeler(
        "/siret",
        {"q": f"siren:{siren} AND etablissementSiege:true", "nombre": 1},
    )

    etablissements = donnees.get("etablissements") or []
    return [_convertir(etablissements[0])] if etablissements else []


def chercher_par_nom(nom, nombre_candidats=5):
    """Recherche par dénomination.

    On interroge l'endpoint `/siret` en filtrant sur les sièges : c'est le seul
    moyen d'obtenir en **un seul appel** la dénomination, l'état *et* l'adresse
    (l'endpoint `/siren` ne porte pas d'adresse).

    Args:
        nom (str): raison sociale.
        nombre_candidats (int): nombre de candidats à rapatrier.

    Returns:
        tuple[list[dict], int]: les candidats au format commun, et le nombre
            total de résultats annoncé par l'INSEE.

    Raises:
        ErreurAPI: en cas d'échec de l'appel.
    """
    nom_propre = _assainir_nom(nom)
    if not nom_propre:
        return [], 0

    donnees = _appeler(
        "/siret",
        {
            "q": f'denominationUniteLegale:"{nom_propre}" AND etablissementSiege:true',
            "nombre": nombre_candidats,
        },
    )

    etablissements = donnees.get("etablissements") or []
    total = (donnees.get("header") or {}).get("total", len(etablissements))
    return [_convertir(e) for e in etablissements], total
