"""Client de l'API Recherche d'entreprises (Séance 4 : `requests` + status_code).

Source : https://recherche-entreprises.api.gouv.fr — base officielle des
entreprises françaises, **accès libre, sans clé d'API**.

Contraintes relevées dans la documentation officielle et respectées ici :
  - **7 requêtes/seconde maximum par adresse IP** (30/s par ASN) → c'est ce qui
    plafonne notre parallélisme à 5 threads par défaut, et non la machine ;
  - au-delà, le serveur répond **HTTP 429** avec un en-tête `Retry-After`
    indiquant le délai à respecter → on le lit et on attend réellement ;
  - un **User-Agent explicite** est recommandé → on en envoie un.

Ce module ne fait *que* parler à l'API. Il ne sait rien du métier « prospect »
et n'écrit aucun fichier : il renvoie du JSON déjà décodé, ou lève `ErreurAPI`.
"""

import logging
import threading
import time

import requests

URL_RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search"
USER_AGENT = "verif-prospects-esgi/1.0 (projet pedagogique ESGI - Python)"

DELAI_TIMEOUT = 10          # secondes, par requête (connexion + lecture)
TENTATIVES_MAX = 3          # nombre total d'essais pour une même requête
ATTENTE_BASE = 1.0          # secondes, doublée à chaque nouvelle tentative
ATTENTE_MAX = 15.0          # plafond de sécurité pour un `Retry-After` fantaisiste

# Codes pour lesquels réessayer a un sens : surcharge ou panne passagère.
# Un 404 ou un 400 sont définitifs, inutile d'insister.
CODES_A_REESSAYER = (429, 500, 502, 503, 504)


class ErreurAPI(Exception):
    """Échec d'un appel API, avec un message destiné au rapport final."""


# `requests.Session` réutilise la connexion TCP (gain notable sur N appels),
# mais n'est pas garantie thread-safe. On donne donc une session *par thread*
# via threading.local() : chaque thread du pool a la sienne.
_local = threading.local()


def _session():
    """Renvoie la `Session` requests propre au thread courant."""
    if not hasattr(_local, "session"):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _local.session = session
    return _local.session


def _duree_attente(reponse, tentative):
    """Calcule le temps d'attente avant la prochaine tentative.

    On privilégie l'en-tête `Retry-After` renvoyé par le serveur ; à défaut on
    applique un back-off exponentiel (1s, 2s, 4s…).

    Args:
        reponse (requests.Response | None): la réponse reçue, si on en a une.
        tentative (int): numéro de la tentative qui vient d'échouer (1, 2, …).

    Returns:
        float: nombre de secondes à attendre.
    """
    if reponse is not None:
        entete = reponse.headers.get("Retry-After")
        if entete:
            try:
                # `Retry-After` peut aussi contenir une date HTTP ; on ne gère
                # que la forme « nombre de secondes », la seule utilisée ici.
                return min(float(entete), ATTENTE_MAX)
            except ValueError:
                logging.debug(f"Retry-After illisible : {entete!r}")

    return min(ATTENTE_BASE * (2 ** (tentative - 1)), ATTENTE_MAX)


def rechercher(parametres):
    """Appelle `/search` et renvoie la réponse JSON décodée.

    Args:
        parametres (dict): paramètres de requête (`q`, `per_page`, …).

    Returns:
        dict: le corps JSON de la réponse.

    Raises:
        ErreurAPI: après épuisement des tentatives, ou sur une erreur définitive
            (paramètres refusés, JSON illisible, réseau injoignable).
    """
    derniere_erreur = "erreur inconnue"

    for tentative in range(1, TENTATIVES_MAX + 1):
        reponse = None
        try:
            logging.debug(f"GET {URL_RECHERCHE} {parametres} (essai {tentative})")
            reponse = _session().get(
                URL_RECHERCHE, params=parametres, timeout=DELAI_TIMEOUT
            )

        # --- Pannes réseau : on distingue les cas pour un message utile ---
        except requests.Timeout:
            derniere_erreur = f"délai dépassé (> {DELAI_TIMEOUT}s)"
        except requests.ConnectionError:
            derniere_erreur = "connexion impossible (réseau ou DNS)"
        except requests.RequestException as erreur:
            # Filet de sécurité : toute autre erreur de la bibliothèque.
            derniere_erreur = f"erreur requests : {erreur}"

        else:
            # --- Séance 4, exo 5 : on vérifie le code AVANT d'exploiter ---
            if reponse.status_code == 200:
                try:
                    return reponse.json()
                except ValueError:
                    # Réponse 200 mais corps non-JSON (page d'erreur d'un proxy,
                    # portail captif…). Inutile de réessayer.
                    raise ErreurAPI(
                        "réponse illisible : le serveur n'a pas renvoyé du JSON"
                    ) from None

            if reponse.status_code in CODES_A_REESSAYER:
                derniere_erreur = f"HTTP {reponse.status_code}"
                if reponse.status_code == 429:
                    derniere_erreur += " (quota de 7 requêtes/s dépassé)"
            else:
                # Erreur définitive : l'API explique souvent pourquoi dans un
                # champ `erreur`, on le remonte tel quel.
                detail = ""
                try:
                    detail = reponse.json().get("erreur", "")
                except ValueError:
                    detail = ""
                raise ErreurAPI(
                    f"HTTP {reponse.status_code}" + (f" — {detail}" if detail else "")
                )

        # On arrive ici uniquement si un nouvel essai est envisageable.
        if tentative < TENTATIVES_MAX:
            attente = _duree_attente(reponse, tentative)
            logging.warning(
                f"{derniere_erreur} — nouvelle tentative dans {attente:.1f}s "
                f"({tentative}/{TENTATIVES_MAX - 1})"
            )
            time.sleep(attente)

    raise ErreurAPI(f"{derniere_erreur} après {TENTATIVES_MAX} tentatives")


def chercher_par_identifiant(identifiant):
    """Recherche directe par SIREN (9 chiffres) ou SIRET (14 chiffres).

    L'API bascule automatiquement en « recherche directe » quand `q` ne contient
    que 9 ou 14 chiffres : le résultat est alors exact, pas approché.

    Args:
        identifiant (str): SIREN ou SIRET, chiffres uniquement.

    Returns:
        list[dict]: les entreprises trouvées (0 ou 1 élément en pratique).

    Raises:
        ErreurAPI: en cas d'échec de l'appel.
    """
    donnees = rechercher({"q": identifiant, "minimal": "true", "include": "siege"})
    return donnees.get("results") or []


def chercher_par_nom(nom, nombre_candidats=5):
    """Recherche textuelle par dénomination.

    Args:
        nom (str): raison sociale ou nom commercial.
        nombre_candidats (int): nombre de candidats à rapatrier pour pouvoir
            détecter une homonymie.

    Returns:
        tuple[list[dict], int]: les candidats et le nombre total de résultats
            annoncé par l'API (utile pour repérer une recherche trop vague).

    Raises:
        ErreurAPI: en cas d'échec de l'appel.
    """
    donnees = rechercher(
        {
            "q": nom,
            "per_page": nombre_candidats,
            "minimal": "true",
            "include": "siege",
        }
    )
    return donnees.get("results") or [], donnees.get("total_results", 0)
