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

**Notre outil** prend un CSV de prospects et produit trois livrables : le
tableau complet vérifié, la liste des prospects à ne pas démarcher, et un
rapport JSON exploitable par un autre script.

### Source de données

**API Recherche d'entreprises** — `https://recherche-entreprises.api.gouv.fr`
Base officielle des entreprises françaises (adossée à Sirene / INSEE),
**accès libre, sans clé d'API**.

> L'API INSEE Sirene, plus complète, a été écartée : elle impose une inscription
> et une clé, ce qui empêcherait de lancer le projet sur n'importe quelle
> machine sans configuration préalable.

---

## 2. Installation et lancement

Prérequis : **Python 3.9+** et une connexion internet. Une seule dépendance
externe, `requests` (vue en Séance 4).

```bash
cd SOUTENANCE
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

Lancement sur le jeu d'essai fourni :

```bash
python3 verif_prospects.py donnees/prospects.csv
```

Les livrables sont écrits dans `resultats/` et une synthèse s'affiche à l'écran.

### Options

| Option | Effet |
|---|---|
| `--sortie DOSSIER` | Dossier des livrables (défaut : `resultats/`) |
| `--jours-recent N` | Une cessation de moins de N jours est « récente » (défaut : 365) |
| `--workers N` | Appels API simultanés, 1 à 7 (défaut : 5) |
| `--sequentiel` | Désactive la parallélisation (= `--workers 1`) |
| `--limite N` | Ne traiter que les N premiers prospects (utile en démo) |
| `--verbose` | Détail de chaque appel API (niveau DEBUG) |

```bash
python3 verif_prospects.py mes_prospects.csv --jours-recent 90 --verbose
```

### Codes de retour

Le script est utilisable dans un `cron` ou une CI :

| Code | Signification |
|---|---|
| `0` | Tous les prospects sont actifs et vérifiés |
| `1` | Erreur bloquante (fichier d'entrée ou écriture impossible) |
| `2` | Traitement réussi, **mais des prospects sont à signaler** |
| `130` | Interrompu par l'utilisateur (Ctrl+C) |

---

## 3. Format d'entrée

Un CSV avec **au moins** une colonne nom **ou** une colonne identifiant.
Les intitulés sont tolérants (insensibles à la casse) :

| Donnée | Intitulés acceptés |
|---|---|
| Nom | `nom`, `nom_entreprise`, `entreprise`, `raison_sociale`, `societe` |
| Identifiant | `siren`, `siret`, `identifiant`, `id` |
| Contact | `contact`, `email`, `mail`, `commercial` |

```csv
nom;siren;contact
ORANGE;380129866;marie.leroy@cabinet.fr
Société Générale;;sonia.bak@cabinet.fr
```

Le lecteur absorbe les défauts d'un export réel :

- **séparateur** deviné automatiquement (`;` `,` tabulation `|`) ;
- **encodage** UTF-8 avec repli cp1252 (exports Excel français) ;
- SIREN copiés-collés avec des espaces (`380 129 866`) nettoyés ;
- lignes vides ignorées, avec un avertissement.

Si un SIREN est fourni, il est privilégié : la recherche est alors **exacte**.
Sans SIREN, on cherche par nom — c'est **approché**, et l'outil le dit
(voir `score_correspondance`).

---

## 4. Livrables produits

### `prospects_verifies.csv`

Une ligne par prospect, **27 colonnes toujours présentes** (même en cas
d'échec), en `;` et UTF-8 avec BOM pour s'ouvrir directement dans Excel.

Colonnes principales :

| Colonne | Contenu |
|---|---|
| `statut_verification` | `VERIFIE` · `INCERTAIN` · `INTROUVABLE` · `IDENTIFIANT_INVALIDE` · `ERREUR_API` |
| `etat_activite` | `ACTIVE` · `CESSEE` · `INCONNU` |
| `alerte` | Code d'alerte (vide si tout va bien) |
| `a_signaler` | `oui` / `non` — la ligne demande un œil humain |
| `message` | Phrase explicative en français, lisible par un commercial |
| `nom_officiel`, `siren`, `siret_siege` | Identité officielle |
| `adresse_siege`, `code_postal`, `commune` | Adresse à jour |
| `date_cessation`, `jours_depuis_cessation`, `cessation_recente` | Cœur de la vérification |
| `score_correspondance` | 0-100 ; 100 = recherche exacte par SIREN |
| `candidats_alternatifs` | Homonymes détectés, à trancher à l'œil |
| `fraicheur_donnee` | Ancienneté de l'information à la source (INSEE/RNE) |
| `url_annuaire` | Lien direct pour un contrôle manuel |

Codes d'`alerte` possibles :

| Code | Signification |
|---|---|
| `CESSATION_RECENTE` | Cessée depuis moins de `--jours-recent` → **prioritaire** |
| `CESSEE` | Cessée depuis plus longtemps |
| `SIEGE_FERME` | Active mais siège fermé / plus aucun établissement ouvert |
| `ETAT_INCONNU` | État administratif absent de la base |
| `CORRESPONDANCE_INCERTAINE` | Homonymes, ou nom saisi incohérent avec le SIREN |
| `INTROUVABLE` | Absente de la base officielle |
| `IDENTIFIANT_INVALIDE` | SIREN/SIRET mal saisi (rejeté sans appel API) |
| `ERREUR_API` | Vérification impossible → **à relancer** |

### `alertes.csv`

Le même format, restreint aux lignes `a_signaler = oui`. C'est le
« signalement clair » demandé par le client : le fichier qu'un commercial ouvre
avant de décrocher son téléphone.

### `rapport.json`

```json
{
  "meta": {
    "genere_le": "2026-09-08T15:49:23",
    "source": "API Recherche d'entreprises (recherche-entreprises.api.gouv.fr)",
    "parametres": { "jours_recent": 365, "workers": 5, "duree_secondes": 1.28 },
    "resume": { "total": 12, "par_etat": { "ACTIVE": 5, "CESSEE": 2 }, "a_signaler": 9 }
  },
  "prospects": [ { "...": "une fiche complète par prospect" } ]
}
```

Le bloc `meta` rend le rapport **auditable** : on sait quand il a été produit,
avec quels paramètres, et sur quelle source.

---

## 5. Architecture

Un rôle = un fichier. Aucun module ne dépasse ~250 lignes.

```
SOUTENANCE/
├── verif_prospects.py          Point d'entrée : CLI, orchestration, parallélisation
├── verificateur/
│   ├── journal.py              Configuration du logging (logs sur stderr)
│   ├── entrees.py              Lecture + validation du CSV (regex, clé de Luhn)
│   ├── api_entreprises.py      Client API : requests, timeout, retry, HTTP 429
│   ├── analyse.py              Métier : rapprochement, statut, alertes (0 réseau)
│   └── sorties.py              Écriture CSV / JSON + rapport console
├── donnees/prospects.csv       Jeu d'essai (cas nominaux + cas limites réels)
├── tests/test_verificateur.py  42 tests, hors ligne
├── requirements.txt
└── README.md
```

Deux principes structurants :

1. **`analyse.py` ne fait aucun appel réseau.** Tout le métier est donc testable
   hors ligne, en 6 ms, sans dépendre de la disponibilité de l'API.
2. **Les logs vont sur stderr, le rapport sur stdout.** Le rapport peut donc
   être redirigé proprement :
   `python3 verif_prospects.py prospects.csv > rapport.txt`

### Pourquoi des threads (Séance 7)

Les appels API sont **I/O-bound** : le programme attend le réseau, il ne calcule
pas. Les threads sont donc efficaces malgré le GIL. Mesure sur le jeu d'essai
(12 prospects) :

| Mode | Durée |
|---|---|
| `--sequentiel` | 2,69 s |
| 5 threads (défaut) | 0,64 s |

**Pourquoi 5 threads et pas 50 ?** La documentation de l'API impose **7 requêtes
par seconde et par adresse IP**. Au-delà, le serveur répond `429 Too Many
Requests` : on ne gagnerait pas de temps, on en perdrait en attentes. `--workers`
est donc borné à 7, et 5 laisse une marge de sécurité.

---

## 6. Robustesse

Ce qui est géré explicitement, et vérifié par les tests :

| Situation | Comportement |
|---|---|
| Fichier d'entrée absent / illisible / vide | Message clair, code retour `1` |
| Colonnes inconnues | Message listant les intitulés attendus |
| Encodage cp1252, séparateur inhabituel | Détectés automatiquement |
| SIREN mal saisi | Rejeté par la **clé de Luhn**, sans dépenser d'appel API |
| API injoignable / timeout | 3 tentatives, back-off exponentiel, puis `ERREUR_API` |
| HTTP 429 (quota dépassé) | En-tête `Retry-After` lu et respecté |
| HTTP 5xx | Nouvelle tentative ; les 4xx définitifs échouent tout de suite |
| Réponse 200 mais non-JSON | Détectée, pas de plantage |
| Champ absent de la réponse | `.get()` partout, aucun `KeyError` |
| Entreprise cessée **sans** date de cessation | Cas réel rencontré, signalé sans planter |
| `etat_administratif` à `null` | Devient `INCONNU`, **jamais** « active » |
| Erreur inattendue dans un thread | Journalisée, la ligne devient `ERREUR_API`, le lot continue |
| `Ctrl+C` | Sortie propre sans traceback, code `130` |

**Règle de conception assumée :** une erreur d'API n'est **jamais** interprétée
comme « entreprise active ». Un outil de vérification qui affirme à tort qu'une
société est ouverte est plus dangereux que celui qui dit « je ne sais pas, à
relancer ».

### Tests

```bash
python3 -m unittest discover -s tests -v
```

42 tests, **aucun appel réseau** : les réponses de l'API sont rejouées depuis
des captures réelles, y compris les cas tordus rencontrés en explorant la base.
La date de référence est figée dans les tests, sinon ceux qui portent sur
« cessation récente » finiraient par échouer tout seuls avec le temps.

---

## 7. Démonstration

```bash
# 1. Cas nominal complet — 12 prospects, dont 4 cas limites réels
python3 verif_prospects.py donnees/prospects.csv

# 2. Le gain de la parallélisation (Séance 7)
python3 verif_prospects.py donnees/prospects.csv --sequentiel
python3 verif_prospects.py donnees/prospects.csv --workers 5

# 3. Le seuil métier est un paramètre : la même cessation change de gravité
python3 verif_prospects.py donnees/prospects.csv --jours-recent 90

# 4. Robustesse — fichier absent
python3 verif_prospects.py nexiste_pas.csv ; echo "code retour : $?"

# 5. Robustesse — API injoignable (on casse l'URL volontairement)
python3 -c "
import sys; sys.argv = ['x', 'donnees/prospects.csv', '--limite', '3']
from verificateur import api_entreprises
api_entreprises.URL_RECHERCHE = 'https://api-injoignable.invalid/search'
import verif_prospects; verif_prospects.main()"

# 6. Les tests, hors ligne
python3 -m unittest discover -s tests -v
```

Le jeu d'essai `donnees/prospects.csv` contient volontairement des cas réels
qui cassent une implémentation naïve :

| Ligne | Cas |
|---|---|
| `ORANGE` / `CARREFOUR` | Nominal, entreprises actives |
| `FREDERIC CONSEIL` (851643189) | **Cessation récente** datée |
| `BOULANGERIE DE L'EUROPE` (923804504) | Cessée **sans** date de fermeture renseignée |
| `JACQUES JUND` (999999998) | SIREN valide, `etat_administratif` à `null` |
| `Société Générale` | Recherche par **nom seul**, sans SIREN |
| `Dupont Consulting` | Nom trop vague → **homonymes** signalés |
| `000000000` | SIREN valide (Luhn) mais absent de la base |
| `123456789` | Clé de Luhn invalide → rejeté sans appel API |
| `12345678` | 8 chiffres → ni SIREN ni SIRET |
| `ORANGE MAIS FAUX NOM` (380129866) | SIREN correct, nom incohérent → signalé |

---

## 8. Répartition du travail

Le découpage en modules a été choisi pour que **chacun ait un fichier à lui**,
avec une interface claire entre les modules (une fonction, un dictionnaire de
sortie), afin de pouvoir travailler en parallèle sans se bloquer.

| Personne | Lot | Fichiers |
|---|---|---|
| **1 — Chef de projet** | Cadrage du besoin, CLI et orchestration, parallélisation, README | `verif_prospects.py`, `journal.py`, `README.md` |
| **2 — Entrées** | Lecture du CSV, validation SIREN (regex + Luhn), tolérance encodage/séparateur | `entrees.py` |
| **3 — API** | Client de l'API, gestion des erreurs réseau, retry, quota 429 | `api_entreprises.py` |
| **4 — Métier & livrables** | Rapprochement des noms, règles d'alerte, écriture CSV/JSON | `analyse.py`, `sorties.py` |

**Travail commun :** le contrat de sortie (les 27 colonnes de `analyse.fiche_vide()`)
a été fixé ensemble en premier — c'est lui qui a permis aux lots 2, 3 et 4
d'avancer en parallèle. Les tests ont été écrits en binôme (2+4), et
l'exploration de l'API pour trouver les cas limites en binôme (1+3).

---

## 9. Limites connues

Ces limites sont **volontairement assumées**, pas des oublis :

- **Le périmètre est franco-français.** L'API ne couvre que les entreprises
  immatriculées en France.
- **Les entreprises non diffusibles sont invisibles.** Un prospect qui a demandé
  à ne pas figurer dans les données publiques ressort `INTROUVABLE` : c'est une
  limite de la source, pas un bug. C'est pour ça que `INTROUVABLE` déclenche une
  alerte au lieu d'être ignoré.
- **`date_fermeture` n'est pas toujours renseignée**, même pour une société
  déclarée cessée. On sait alors *qu'elle* est fermée, pas *depuis quand*.
- **La recherche par nom est approchée.** Le seuil de ressemblance (75/100) a été
  réglé à la main sur notre jeu d'essai. Fournir le SIREN reste toujours
  préférable.
- **Pas de cache.** Deux exécutions consécutives refont tous les appels.
  Acceptable sur quelques centaines de prospects, à revoir au-delà.

## 10. Pistes d'évolution

Par ordre de rapport valeur / effort, en reprenant les souhaits du client :

1. **Cache local** (SQLite ou fichier JSON daté) : ne réinterroger que les
   prospects dont la donnée dépasse N jours. Indispensable pour passer à
   plusieurs milliers de lignes.
2. **Envoi automatique du rapport par mail** (`smtplib`), déclenché par un
   `cron` quotidien — le « mail en daily » du brainstorm. Le code de retour `2`
   est déjà prévu pour ça : le cron n'alerte que s'il y a des signalements.
3. **Données financières** (CA, résultat) : l'API expose un champ `finances`
   pour une partie des entreprises. Permettrait un premier indicateur de
   solvabilité — le point 3 du « Rêve Client ».
4. **Score de risque de fermeture** : combiner l'âge de l'entreprise, la
   tranche d'effectif, le nombre d'établissements ouverts et la fraîcheur de la
   donnée. À présenter comme un **indice de vigilance**, jamais comme une
   prédiction — la « garantie 100 % » souhaitée par le client n'est pas
   atteignable à partir de données déclaratives publiées avec plusieurs mois de
   décalage.
5. **Croisement avec l'API INSEE Sirene** pour les champs absents de l'API
   ouverte, une fois une clé obtenue.
