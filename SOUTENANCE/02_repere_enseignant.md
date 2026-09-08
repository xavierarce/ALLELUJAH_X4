# REPÈRE ENSEIGNANT — non destiné à être distribué tel quel aux étudiants

(Fiche de cadrage du SUJET 4 — Conseil / B2B)

## Exemple d'interprétation

*Une reformulation possible du besoin (parmi d'autres tout aussi valables) :*

> Vérifier automatiquement, pour chaque prospect, si l'entreprise est toujours
> en activité et récupérer ses informations à jour (adresse, statut), avant tout
> démarchage commercial.

## Source de données / API potentielle

### API Recherche d'entreprises (`recherche-entreprises.api.gouv.fr`)
Recherche et vérification d'entreprises françaises par nom ou SIREN, statut
d'activité inclus. **Accès libre, sans clé.**

### API INSEE Sirene (alternative)
Base de référence officielle des entreprises françaises, plus complète mais
**nécessite une inscription pour la clé d'accès**.

## Champs potentiellement utiles

| Champ |
|---|
| Nom de l'entreprise |
| SIREN / SIRET |
| Statut (active / cessée) et date de cessation éventuelle |
| Adresse du siège |

## Piste de filtrage métier

Filtrage sur le statut (ne garder que les entreprises actives), ou signalement
des cessations récentes.

## Exemple de sortie attendue

Un fichier structuré listant chaque prospect avec son statut vérifié, avec un
signalement clair pour les entreprises qui ne sont plus en activité.

---

## Notes manuscrites — « Rêve Client » (annotations au stylo rouge)

Fonctionnalités visées, de la base à l'ambitieux :

1. **Vérif statut** pour une liste d'entreprises
2. **Signaler les cessations récentes**
3. **CA + solvabilité** (chiffre d'affaires + capacité à payer)
4. **Prédire une fermeture**
5. **Garantie 100 %**
