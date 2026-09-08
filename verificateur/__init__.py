# Package `verificateur` — cœur métier du vérificateur de prospects B2B.
#
# Découpage volontaire en modules (un rôle = un fichier) :
#   entrees.py   lecture + validation du JSON de prospects (Séances 5-6)
#   api.py       client de l'API Recherche d'entreprises (Séance 4)
#   analyse.py   état d'activité, cessations récentes, correspondance de nom
#   sorties.py   écriture du rapport JSON (Séance 6)

__version__ = "2.0.0"
