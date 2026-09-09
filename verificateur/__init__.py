# Package `verificateur` — cœur métier du vérificateur de prospects B2B.
#
# Un rôle par fichier, une porte d'entrée par fichier :
#   entrees.py   charger_prospects()  le JSON du cabinet → objets Prospect
#   api.py       chercher()           un appel à l'API, tentatives comprises
#   analyse.py   analyser()           réponse de l'API → verdict
#   sorties.py   ecrire_rapport()     verdicts → resultats/rapport.json
