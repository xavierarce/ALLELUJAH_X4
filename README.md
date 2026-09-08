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

**Tout est en JSON**, de l'entrée à la sortie : un seul format à connaître, et
il correspond nativement à ce que renvoie l'API.

---

## 2. Installation et lancement

Prérequis : **Python 3.9+** et une connexion internet. Une seule dépendance
externe, `requests` (vue en Séance 4).

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python3 verif_prospects.py donnees/prospects.json
```

### Options

| Option | Effet |
|---|---|
| `--sortie DOSSIER` | Dossier du rapport (défaut : `resultats/`) |
| `--sequentiel` | Un seul appel API à la fois, au lieu de 5 en parallèle |
| `--verbose` | Affiche chaque appel API (niveau DEBUG) |

### Codes de retour

Le script est utilisable dans un `cron` : `0` = tout va bien, `2` = il reste des
prospects à signaler, `1` = erreur (fichier illisible, écriture impossible),
`130` = interrompu au clavier.

---

## 3. Format d'entrée

Un tableau JSON, un objet par prospect, avec au moins un `nom` **ou** un
identifiant :

```json
[
  { "nom": "ORANGE", "siren": "380129866", "contact": "marie.leroy@cabinet.fr" },
  { "nom": "Société Générale", "contact": "sonia.bak@cabinet.fr" },
  { "siret": "38012986646013" }
]
```

- **Clés reconnues** : `nom`, `siren` ou `siret`, `contact` (champ libre, repris
  tel quel dans le rapport). Toute autre clé est ignorée.
- Le tableau peut aussi être emballé dans `{"prospects": [...]}`.
- Un identifiant écrit en nombre (`"siren": 380129866`) ou avec des espaces
  (`"380 129 866"`) est accepté.
- Avec un SIREN/SIRET la vérification est **exacte** ; sans identifiant, on
  cherche par nom et le résultat est **approché** (voir `score_correspondance`).

---

## 4. Le livrable : `resultats/rapport.json`

```json
{
  "meta": {
    "genere_le": "2026-09-08T16:42:05",
    "source": "API Recherche d'entreprises (recherche-entreprises.api.gouv.fr)",
    "parametres": { "fichier_entree": "donnees/prospects.json", "workers": 5,
                    "duree_secondes": 0.57 },
    "resume": { "total": 12, "a_signaler": 8,
                "par_etat": { "ACTIVE": 5, "CESSEE": 2, "INCONNU": 5 },
                "par_alerte": { "CESSATION_RECENTE": 1, "CESSEE": 1, "…": 1 } }
  },
  "prospects": [
    {
      "rang": 3,
      "nom_saisi": "FREDERIC CONSEIL",
      "identifiant_saisi": "851643189",
      "contact": "paul.durand@cabinet.fr",
      "statut_verification": "VERIFIE",
      "etat_activite": "CESSEE",
      "alerte": "CESSATION_RECENTE",
      "a_signaler": true,
      "message": "Cessation d'activité le 2025-09-19 (il y a 354 jours) — ne pas démarcher.",
      "nom_officiel": "FREDERIC CONSEIL (TAXI SERVICES 22)",
      "siren": "851643189",
      "siret_siege": "85164318900026",
      "adresse_siege": "26 RUE DOCTEUR ROUX 22000 SAINT-BRIEUC",
      "date_creation": "2019-07-01",
      "date_cessation": "2025-09-19",
      "score_correspondance": 100,
      "url_annuaire": "https://annuaire-entreprises.data.gouv.fr/entreprise/851643189"
    }
  ]
}
```

Les champs clés :

- `etat_activite` — `ACTIVE` / `CESSEE` / `INCONNU` : la réponse à la question du client ;
- `alerte` — `CESSATION_RECENTE`, `CESSEE`, `ETAT_INCONNU`,
  `CORRESPONDANCE_INCERTAINE`, `INTROUVABLE`, `IDENTIFIANT_INVALIDE`, `ERREUR_API` ;
- `a_signaler` — un **booléen JSON**, pas une chaîne : exploitable sans conversion ;
- `message` — la phrase à lire avant de décrocher le téléphone ;
- `nom_officiel`, `siren`, `siret_siege`, `adresse_siege` — les infos à jour ;
- `score_correspondance` — 100 si la recherche s'est faite par identifiant.

**Toutes les fiches ont exactement les mêmes clés**, même celles en échec (c'est
le contrat fixé par `analyse.fiche_vide()`, et un test le vérifie) : un script
en aval n'a jamais à tester la présence d'un champ.

### Extraire les prospects à ne pas démarcher

```bash
jq '.prospects[] | select(.a_signaler) | {nom_officiel, alerte, message}' resultats/rapport.json
```

Sans `jq`, en une ligne de Python :

```bash
python3 -c "import json; r=json.load(open('resultats/rapport.json')); print(*[p['nom_officiel'] or p['nom_saisi'] for p in r['prospects'] if p['a_signaler']], sep='\n')"
```

---

## 5. Architecture

Un rôle par fichier, pour que chaque module soit lisible et testable seul :

```
verif_prospects.py          196 l.  CLI (argparse) + orchestration + threads   Séances 4 et 7
verificateur/entrees.py     227 l.  JSON → objets Prospect, regex + Luhn       Séances 5 et 6
verificateur/api.py         132 l.  appels HTTP, status_code, retry, timeout   Séance 4
verificateur/analyse.py     385 l.  état d'activité, alertes, correspondance   Séance 5
verificateur/sorties.py     114 l.  écriture du rapport JSON, synthèse console Séance 6
tests/test_verificateur.py  404 l.  42 tests, hors ligne
```

`analyse.py` ne fait **aucun appel réseau** et `api.py` ne connaît **rien** du
métier : c'est ce qui rend le cœur du programme testable hors ligne.

### Pourquoi des threads (Séance 7)

Les appels API sont **I/O-bound** : le programme attend le réseau, il ne calcule
pas. Les threads sont donc utiles malgré le GIL. Mesuré sur les 12 prospects du
jeu d'essai, deux exécutions à la suite :

| Mode | Durée |
|---|---|
| `--sequentiel` | 4,12 s |
| 5 threads (défaut) | 0,69 s |

Le plafond de 5 threads ne vient pas de la machine mais de l'API, qui autorise
**7 requêtes/seconde par adresse IP** : au-delà elle répond `429`.

---

## 6. Robustesse

Le programme ne s'arrête jamais sur un prospect. Chaque cas produit une fiche
honnête dans le rapport :

| Situation | Comportement |
|---|---|
| Fichier absent, JSON mal formé | message avec **la ligne et la colonne** fautives, code `1` |
| JSON valide mais pas un tableau | message clair, code `1` |
| Entrée qui n'est pas un objet, ou sans nom ni identifiant | ignorée avec un `WARNING` |
| SIREN mal saisi (clé de Luhn) | rejeté **sans dépenser d'appel API** |
| `HTTP 429`, `500`, `503`, timeout | 3 tentatives espacées, puis `ERREUR_API` |
| `HTTP 400` / `404` | échec immédiat (insister ne sert à rien) |
| API injoignable | toutes les fiches en `ERREUR_API`, **jamais** en « active » |
| Champ absent de la réponse | `.get()` partout, la fiche reste complète |
| Société marquée cessée sans date | signalée quand même, mention « date non renseignée » |
| `Ctrl+C` | sortie propre, sans traceback |

Une erreur d'API n'est **pas** une entreprise active : elle ressort en `INCONNU`
et à relancer. C'est le point qui évite de rendre un livrable trompeur.

### Tests

```bash
python3 -m unittest discover -s tests -v
```

42 tests, **aucun accès réseau** : les réponses de l'API sont rejouées à partir
de cas réels (y compris une société `etat_administratif = null` et une société
cessée sans date de fermeture), et `requests.get` est remplacé par un faux pour
tester le `429`, le `400` et le timeout.

---

## 7. Démonstration

```bash
# 1. Cas nominal — 12 prospects, dont 5 cas limites réels
python3 verif_prospects.py donnees/prospects.json

# 2. Le gain de la parallélisation (Séance 7)
python3 verif_prospects.py donnees/prospects.json --sequentiel

# 3. Robustesse — fichier absent, puis JSON mal formé
python3 verif_prospects.py donnees/inexistant.json ; echo "code = $?"
printf '[{"nom":"X"},]' > /tmp/casse.json
python3 verif_prospects.py /tmp/casse.json ; echo "code = $?"

# 4. Les tests, hors ligne
python3 -m unittest discover -s tests -v
```

Le jeu d'essai `donnees/prospects.json` contient volontairement : une cessation
récente, une société cessée sans date, un état administratif absent, un SIREN
inexistant, une faute de frappe (clé de Luhn), un identifiant trop court, un
nom incohérent avec son SIREN, deux recherches par nom seul et une entrée vide.

---

## 8. Répartition du travail

Le découpage en modules a été choisi pour que **chacun ait un fichier à lui**,
avec une interface claire entre les modules, afin de travailler en parallèle
sans se bloquer.

| Lot | Périmètre | Fichiers |
|---|---|---|
| **Chef de projet** | Cadrage du besoin, CLI, orchestration, parallélisation, README | `verif_prospects.py`, `README.md` |
| **Entrées** | Lecture du JSON, validation SIREN (regex + Luhn) | `entrees.py` |
| **API** | Appels HTTP, erreurs réseau, retry, quota 429 | `api.py` |
| **Métier & livrable** | Règles d'alerte, correspondance de nom, rapport JSON | `analyse.py`, `sorties.py` |

**Travail commun :** le contrat de sortie (les clés de `analyse.fiche_vide()`) a
été fixé ensemble en premier — c'est lui qui a permis aux trois autres lots
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
2. **Historisation** des rapports pour détecter les changements d'état d'un
   passage à l'autre — première brique vers la « prédiction de fermeture ».
3. **Export tableur** pour les commerciaux, généré depuis le rapport JSON (le
   JSON reste la source de vérité, le CSV n'en serait qu'une vue).
4. **API Sirene de l'INSEE** en source de recours : historisée, elle donne les
   dates de cessation que l'API ouverte laisse parfois vides — au prix d'une
   clé d'accès et d'un quota de 30 requêtes/minute.
