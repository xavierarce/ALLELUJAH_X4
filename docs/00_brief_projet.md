# Brief projet — Vérificateur de prospects B2B (Sujet 4)

Synthèse actionnable des deux documents scannés
([sujet client](01_sujet_client.md) + [repère enseignant](02_repere_enseignant.md)).

## Problème (en une phrase)
Un cabinet de conseil perd du temps à vérifier à la main si ses prospects sont
encore en activité — et se fait parfois avoir avec des sociétés déjà fermées.

## Besoin technique reformulé
Pour chaque prospect d'une liste, **vérifier automatiquement** via une base
officielle si l'entreprise est **toujours active** et récupérer ses **infos à
jour** (statut, adresse), puis **signaler** celles qui ont cessé leur activité.

## Source de données
- **Principale :** API Recherche d'entreprises — `https://recherche-entreprises.api.gouv.fr`
  (entreprises FR par nom ou SIREN, statut inclus, **libre, sans clé**).
- **Alternative :** API INSEE Sirene (plus complète, **clé requise**).

## Données à extraire par entreprise
| Champ | Usage |
|---|---|
| Nom de l'entreprise | identification |
| SIREN / SIRET | identifiant unique |
| Statut (active / cessée) + date de cessation | cœur de la vérif |
| Adresse du siège | mise à jour des infos |

## Livrable attendu
Un **fichier structuré** (CSV/JSON) listant chaque prospect avec son statut
vérifié, et un **signalement clair** des entreprises qui ne sont plus actives.

## Périmètre — du minimum à l'ambitieux (notes « Rêve Client »)
1. ✅ **MVP** — vérifier le statut pour une liste d'entreprises
2. ✅ Signaler les **cessations récentes**
3. ➕ **CA + solvabilité** (chiffre d'affaires, capacité à payer)
4. ➕ **Prédire une fermeture** (indicateurs de risque)
5. 🎯 **Garantie 100 %** (objectif idéal de fiabilité)

## Briques techniques mobilisables (vues en TP)
- **Séance 4** — `requests` : appel API + vérif `status_code`.
- **Séance 5** — parsing / regex / JSON : exploiter la réponse.
- **Séance 6** — fichiers + `try/except` : écrire le livrable, robustesse.
- **Séance 7** — `ThreadPoolExecutor` : paralléliser les appels API (I/O-bound)
  pour tenir le volume de prospects.
