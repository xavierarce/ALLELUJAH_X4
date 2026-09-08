# Package `verificateur` — cœur métier du vérificateur de prospects B2B.
#
# Découpage volontairement en modules (un rôle = un fichier) :
#   journal.py          configuration du logging (Séance 3)
#   entrees.py          lecture + validation du CSV de prospects (Séances 5-6)
#   api_entreprises.py  client de l'API Recherche d'entreprises (Séance 4)
#   analyse.py          normalisation, statut d'activité, cessations récentes
#   sorties.py          écriture des livrables CSV / JSON (Séance 6)

__version__ = "1.0.0"
