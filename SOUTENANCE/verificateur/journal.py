"""Configuration centralisée du logging (Séance 3, exo 4).

Tous les modules font `import logging` puis `logging.info(...)`. C'est le point
d'entrée (`verif_prospects.py`) qui appelle `configurer()` **une seule fois**,
au démarrage.

Choix assumé : les logs partent sur **stderr**, pas sur stdout. Le rapport
final reste ainsi seul sur stdout, ce qui permet de le rediriger proprement :

    python3 verif_prospects.py donnees/prospects.csv > rapport.txt
"""

import logging
import sys

FORMAT = "%(asctime)s [%(levelname)-8s] %(message)s"
FORMAT_DATE = "%H:%M:%S"


def configurer(verbose=False):
    """Installe le logging global.

    Args:
        verbose (bool): si ``True``, niveau DEBUG (détail de chaque appel API) ;
            sinon niveau INFO.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=FORMAT,
        datefmt=FORMAT_DATE,
        stream=sys.stderr,
    )
