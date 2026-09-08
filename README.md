# Vérificateur de prospects B2B

Projet de fin de module — **Scripting Python / Cybersécurité** (ESGI)
**SUJET 4 — Conseil / B2B**

---

## 1. Le besoin

Un cabinet de conseil vérifie **à la main** si ses prospects existent encore
avant de les démarcher. C'est long, peu fiable, et il leur est déjà arrivé de
démarcher une société fermée depuis des mois.

**Besoin technique reformulé :** pour chaque prospect d'une liste, vérifier
automatiquement auprès d'une base officielle si l'entreprise est **toujours en
activité**, récupérer ses **informations à jour** (statut, adresse), et
**signaler clairement** celles qui ont cessé leur activité.

**Source de données :** l'[API Recherche d'entreprises](https://recherche-entreprises.api.gouv.fr)
de l'État — base officielle des entreprises françaises, **accès libre, sans clé
d'API**. C'est ce qui permet de lancer le projet sur n'importe quelle machine à
partir des seules instructions de ce README.

---

## 2. Installation et lancement

Prérequis : **Python 3.9+** et une connexion internet. Une seule dépendance
externe, `requests` (vue en Séance 4).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python3 verif_prospects.py donnees/prospects.csv
```

### Options

| Option | Effet |
|---|---|
| `--sortie DOSSIER` | Dossier des livrables (défaut : `resultats/`) |
| `--sequentiel` | Un seul appel API à la fois, au lieu de 5 en parallèle |
| `--verbose` | Affiche chaque appel API (niveau DEBUG) |

### Codes de retour

Le script est utilisable dans un `cron` : `0` = tout va bien, `2` = il reste des
prospects à signaler, `1` = erreur (fichier illisible, écriture impossible),
`130` = interrompu au clavier.

---

## 3. Format d'entrée

Un CSV avec au moins une colonne « nom » **ou** une colonne d'identifiant :

```csv
nom;siren;contact
ORANGE;380129866;marie.leroy@cabinet.fr
Société Générale;;sonia.bak@cabinet.fr
```

- **Intitulés tolérés** — nom : `nom`, `entreprise`, `raison_sociale`, `societe`… ;
  identifiant : `siren`, `siret`, `identifiant` ; contact : `contact`, `email`…
- **Séparateur** (`;` `,` tabulation) et **encodage** (UTF-8 ou cp1252, exports
  Excel français) détectés automatiquement.
- Avec un SIREN/SIRET la vérification est **exacte** ; sans identifiant, on
  cherche par nom et le résultat est **approché** (voir `score_correspondance`).

---

## 4. Livrables produits

| Fichier | Contenu |
|---|---|
| `resultats/prospects_verifies.csv` | le tableau complet, 17 colonnes fixes, ouvrable dans Excel |
| `resultats/alertes.csv` | le sous-ensemble à ne pas démarcher — le « signalement clair » demandé |
| `resultats/rapport.json` | les mêmes données + un bloc `meta` (horodatage, paramètres, compteurs) |

Les colonnes clés :

- `etat_activite` — `ACTIVE` / `CESSEE` / `INCONNU` : la réponse à la question du client ;
- `alerte` — `CESSATION_RECENTE`, `CESSEE`, `ETAT_INCONNU`,
  `CORRESPONDANCE_INCERTAINE`, `INTROUVABLE`, `IDENTIFIANT_INVALIDE`, `ERREUR_API` ;
- `a_signaler` — `oui`/`non`, le filtre à appliquer dans un tableur ;
- `message` — la phrase à lire avant de décrocher le téléphone ;
- `nom_officiel`, `siren`, `siret_siege`, `adresse_siege` — les infos à jour ;
- `score_correspondance` — 100 si la recherche s'est faite par identifiant.

Le CSV est écrit en `utf-8-sig` avec `;` comme séparateur : sans ça, Excel en
configuration française casse les accents et empile tout dans une colonne.

---

## 5. Architecture

Un rôle par fichier, pour que chaque module soit lisible et testable seul :

```
verif_prospects.py          CLI (argparse) + orchestration + threads   Séances 4 et 7
verificateur/entrees.py     CSV → objets Prospect, regex + clé de Luhn Séances 5 et 6
verificateur/api.py         appels HTTP, status_code, retry, timeout    Séance 4
verificateur/analyse.py     état d'activité, alertes, correspondance    Séance 5
verificateur/sorties.py     écriture CSV / JSON, rapport console        Séance 6
tests/test_verificateur.py  40 tests, hors ligne
```

`analyse.py` ne fait **aucun appel réseau** et `api.py` ne connaît **rien** du
métier : c'est ce qui rend le cœur du programme testable hors ligne.

### Pourquoi des threads (Séance 7)

Les appels API sont **I/O-bound** : le programme attend le réseau, il ne calcule
pas. Les threads sont donc utiles malgré le GIL. Mesuré sur les 12 prospects du
jeu d'essai :

| Mode | Durée |
|---|---|
| `--sequentiel` | 2,05 s |
| 5 threads (défaut) | 0,52 s |

Le plafond de 5 threads ne vient pas de la machine mais de l'API, qui autorise
**7 requêtes/seconde par adresse IP** : au-delà elle répond `429`.

---

## 6. Robustesse

Le programme ne s'arrête jamais sur un prospect. Chaque cas produit une ligne
honnête dans le livrable :

| Situation | Comportement |
|---|---|
| Fichier absent / vide / colonnes inconnues | message clair, code de retour `1` |
| Ligne sans nom ni identifiant | ignorée avec un `WARNING` |
| SIREN mal saisi (clé de Luhn) | rejeté **sans dépenser d'appel API** |
| `HTTP 429`, `500`, `503`, timeout | 3 tentatives espacées, puis `ERREUR_API` |
| `HTTP 400` / `404` | échec immédiat (insister ne sert à rien) |
| API injoignable | toutes les lignes en `ERREUR_API`, **jamais** en « active » |
| Champ absent de la réponse | `.get()` partout, la ligne reste complète |
| Société marquée cessée sans date | signalée quand même, mention « date non renseignée » |
| `Ctrl+C` | sortie propre, sans traceback |

Une erreur d'API n'est **pas** une entreprise active : elle ressort en `INCONNU`
et à relancer. C'est le point qui évite de rendre un livrable trompeur.

### Tests

```bash
python3 -m unittest discover -s tests -v
```

40 tests, **aucun accès réseau** : les réponses de l'API sont rejouées à partir
de cas réels (y compris une société `etat_administratif = null` et une société
cessée sans date de fermeture), et `requests.get` est remplacé par un faux pour
tester le `429`, le `400` et le timeout.

---

## 7. Démonstration

```bash
# 1. Cas nominal — 12 prospects, dont 5 cas limites réels
python3 verif_prospects.py donnees/prospects.csv

# 2. Le gain de la parallélisation (Séance 7)
python3 verif_prospects.py donnees/prospects.csv --sequentiel

# 3. Robustesse — fichier absent
python3 verif_prospects.py donnees/inexistant.csv ; echo "code = $?"

# 4. Les tests, hors ligne
python3 -m unittest discover -s tests -v
```

Le jeu d'essai `donnees/prospects.csv` contient volontairement : une cessation
récente, une société cessée sans date, un état administratif absent, un SIREN
inexistant, une faute de frappe (clé de Luhn), un identifiant trop court, un
nom incohérent avec son SIREN, une recherche par nom seul et une ligne vide.

---

## 8. Répartition du travail

Le découpage en modules a été choisi pour que **chacun ait un fichier à lui**,
avec une interface claire entre les modules, afin de travailler en parallèle
sans se bloquer.

| Lot | Périmètre | Fichiers |
|---|---|---|
| **Chef de projet** | Cadrage du besoin, CLI, orchestration, parallélisation, README | `verif_prospects.py`, `README.md` |
| **Entrées** | Lecture du CSV, validation SIREN (regex + Luhn), encodages | `entrees.py` |
| **API** | Appels HTTP, erreurs réseau, retry, quota 429 | `api.py` |
| **Métier & livrables** | Règles d'alerte, correspondance de nom, CSV/JSON | `analyse.py`, `sorties.py` |

**Travail commun :** le contrat de sortie (les colonnes de `analyse.fiche_vide()`)
a été fixé ensemble en premier — c'est lui qui a permis aux trois autres lots
d'avancer en parallèle. Les tests ont été écrits en binôme.

---

## 9. Limites connues et pistes d'évolution

Limites assumées :

- **Entreprises françaises uniquement** — la base ne couvre pas l'étranger.
- **Recherche par nom approchée** : le seuil de ressemblance (75 %) est réglé à
  la main. En dessous, l'outil dit « à confirmer » au lieu de trancher.
- **Pas de chiffre d'affaires** : l'API ne l'expose pas (point 3 du « rêve
  client »). Il faudrait les comptes annuels de l'INPI.
- **Certaines entreprises sont non diffusibles** et ressortent `INTROUVABLE`.

Pistes, dans l'ordre de valeur pour le cabinet :

1. **Envoi automatique du rapport par mail** (`smtplib`) + `cron` quotidien :
   le code de retour `2` est déjà là pour déclencher l'alerte.
2. **Historisation** des exécutions pour détecter les changements d'état d'un
   passage à l'autre — première brique vers la « prédiction de fermeture ».
3. **Solvabilité** via les comptes annuels de l'INPI.
4. **API Sirene de l'INSEE** en source de recours : historisée, elle donne les
   dates de cessation que l'API ouverte laisse parfois vides — au prix d'une
   clé d'accès et d'un quota de 30 requêtes/minute.
