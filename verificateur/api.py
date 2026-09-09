"""Client de l'API Recherche d'entreprises (recherche-entreprises.api.gouv.fr).

Base officielle des entreprises françaises, en accès libre. Renvoie du JSON
décodé ou lève `ErreurAPI` ; ne sait rien du métier « prospect ».
"""

import logging
import time

import requests

URL_RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search"
USER_AGENT = "verif-prospects-esgi/1.0 (projet pedagogique ESGI)"

DELAI_TIMEOUT = 10
TENTATIVES_MAX = 3
ATTENTE = 2.0

# Surcharge ou panne passagère : réessayer a un sens. Un 400 ou un 404 sont
# définitifs, insister ne sert à rien.
CODES_A_REESSAYER = (429, 500, 502, 503, 504)


class ErreurAPI(Exception):
    """Échec d'un appel, avec un message destiné au rapport final."""


def _appeler(parametres):
    """Appelle `/search` et renvoie le JSON décodé."""
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
            # Panne réseau, DNS injoignable, proxy…
            erreur = f"réseau indisponible ({type(exception).__name__})"
        else:
            if reponse.status_code == 200:
                try:
                    return reponse.json()
                except ValueError:
                    # 200 sans JSON : proxy ou portail captif de wifi public.
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

    L'API bascule d'elle-même en recherche exacte quand `requete` ne contient
    que les 9 chiffres d'un SIREN ; sinon elle fait une recherche textuelle,
    dont on rapatrie plusieurs candidats pour pouvoir choisir le bon.
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
