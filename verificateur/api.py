"""Client de l'API Recherche d'entreprises (Séance 4 : `requests` + status_code).

Source : https://recherche-entreprises.api.gouv.fr — base officielle des
entreprises françaises, **accès libre, sans clé d'API**.

Deux contraintes de la documentation officielle sont prises en compte :
  - **7 requêtes/seconde maximum par adresse IP** : c'est ce qui plafonne le
    nombre de threads du programme, et non la machine ;
  - au-delà, le serveur répond **HTTP 429** — un code passager, donc on
    réessaie, contrairement à un 400 ou un 404 qui sont définitifs.

Ce module ne fait *que* parler à l'API : il renvoie du JSON déjà décodé, ou
lève `ErreurAPI`. Il ne sait rien du métier « prospect ».
"""

import logging
import time

import requests

URL_RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search"
USER_AGENT = "verif-prospects-esgi/1.0 (projet pedagogique ESGI)"

DELAI_TIMEOUT = 10      # secondes, par requête (connexion + lecture)
TENTATIVES_MAX = 3      # nombre total d'essais pour une même requête
ATTENTE = 2.0           # secondes entre deux tentatives

# Codes pour lesquels réessayer a un sens : surcharge ou panne passagère.
CODES_A_REESSAYER = (429, 500, 502, 503, 504)


class ErreurAPI(Exception):
    """Échec d'un appel API, avec un message destiné au rapport final."""


def _appeler(parametres):
    """Appelle `/search` et renvoie la réponse JSON décodée.

    Args:
        parametres (dict): paramètres de requête (`q`, `per_page`, …).

    Returns:
        dict: le corps JSON de la réponse.

    Raises:
        ErreurAPI: après épuisement des tentatives, ou sur une erreur
            définitive (paramètres refusés, JSON illisible).
    """
    erreur = "erreur inconnue"

    for tentative in range(1, TENTATIVES_MAX + 1):
        logging.debug(f"GET {URL_RECHERCHE} {parametres} (essai {tentative})")
        try:
            reponse = requests.get(
                URL_RECHERCHE,
                params=parametres,
                timeout=DELAI_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.Timeout:
            erreur = f"délai dépassé (> {DELAI_TIMEOUT} s)"
        except requests.RequestException as exception:
            # Couvre la panne de réseau, le DNS injoignable, le proxy…
            erreur = f"réseau indisponible ({type(exception).__name__})"
        else:
            # Séance 4 : on vérifie le code AVANT d'exploiter la réponse.
            if reponse.status_code == 200:
                try:
                    return reponse.json()
                except ValueError:
                    # 200 mais corps non-JSON : page d'erreur d'un proxy,
                    # portail captif d'un wifi public… Inutile de réessayer.
                    raise ErreurAPI(
                        "réponse illisible : le serveur n'a pas renvoyé du JSON"
                    ) from None

            erreur = f"HTTP {reponse.status_code}"
            if reponse.status_code == 429:
                erreur += " (quota de 7 requêtes/s dépassé)"
            elif reponse.status_code not in CODES_A_REESSAYER:
                raise ErreurAPI(erreur)

        if tentative < TENTATIVES_MAX:
            logging.warning(f"{erreur} — nouvelle tentative dans {ATTENTE:.0f} s")
            time.sleep(ATTENTE)

    raise ErreurAPI(f"{erreur} après {TENTATIVES_MAX} tentatives")


def chercher(requete, nombre_candidats=5):
    """Cherche une entreprise par SIREN ou par dénomination.

    Un seul point d'entrée pour les deux cas : l'API bascule d'elle-même en
    recherche **exacte** quand `q` ne contient que les 9 chiffres d'un SIREN,
    et fait sinon une recherche **textuelle** dont on rapatrie plusieurs
    candidats pour pouvoir choisir le bon.

    Args:
        requete (str): un SIREN (9 chiffres) ou une raison sociale.
        nombre_candidats (int): nombre de résultats à rapatrier.

    Returns:
        list[dict]: les entreprises trouvées, éventuellement vide.

    Raises:
        ErreurAPI: en cas d'échec de l'appel.
    """
    donnees = _appeler(
        {
            "q": requete,
            "per_page": nombre_candidats,
            "minimal": "true",
            "include": "siege",
        }
    )
    return donnees.get("results") or []
