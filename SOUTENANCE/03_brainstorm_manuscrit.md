# Brainstorm manuscrit — cadrage du projet (Séance 5, à la main)

Transcription fidèle de la feuille de notes. Structure conservée ; mots
incertains signalés par `[?]`. Le texte barré est noté ~~barré~~.

## Partie haute — pistes en vrac

- **Societe.com / Pappers / [l']officiel** → **API INSEE + Recherche d'entreprise**
- ~~page web~~ ~~streamlit~~ → **API / ncurses** [?] ( → **mail / automatique ou Excel** )
- À partir de quelle donnée ? (**nom / SIRET**)
- **Automatique ?** depuis un **CSV** ou **recherche manuelle**
- **Extract tout** ou vue plutôt **générique ?**
  - (une flèche remonte de **CA** vers « extract » → on veut aussi extraire le CA)

### Colonne de droite (idées de sortie / contexte)
- **SIRET / SIREN** du client
- **Fermé / ouverte** (statut)
- **Excel dans un mail**
- **BDD** (base de données)

### Idées barrées (écartées)
- ~~ajout d'entreprise ?~~
- ~~CSV / Excel ?~~
- ~~dernière date des données~~ → ~~validité~~

## Partie basse — schéma de flux retenu

```
   INSEE        Recherche entreprise
      \                /
       v              v
              api  ──►  mail en daily  ──►  prof
       ^
       │
      csv  ◄────────  page d'ajout d'entreprise
      │ │ │ │ │
      │ │ │ │ └─►  siret
      │ │ │ └───►  siren
      │ │ └─────►  nom
      │ └───────►  CA
      │ └───────►  autres
      │
      └─►  date de mise à jour des données (pour estimer la validité)
      └─►  prédire fermeture entreprise
```

### Lecture du schéma
1. Deux sources officielles : **API INSEE Sirene** et **API Recherche d'entreprises**.
2. On les interroge via **`api`**, qui envoie un **mail quotidien (« daily »)** au **prof** (livrable de suivi).
3. L'entrée se fait par un **CSV** (alimenté par une **page d'ajout d'entreprise**).
4. Chaque ligne CSV porte les colonnes : **siret, siren, nom, CA, autres**.
5. Deux fonctionnalités avancées visées :
   - **date de mise à jour des données** → pour **estimer la validité** de l'info.
   - **prédire la fermeture** d'une entreprise.

## Synthèse des décisions (ce que le brainstorm a tranché)
- **Sources :** API INSEE + API Recherche d'entreprises (les officielles ;
  Societe.com / Pappers évoqués mais on part sur les API gouv).
- **Entrée :** un **CSV** de prospects (clé = **nom** ou **SIRET**).
- **Sortie :** fichier **Excel / CSV**, éventuellement envoyé par **mail**
  (idée d'un envoi **quotidien** automatique).
- **Interface :** page web / Streamlit **écartés** ; piste **API + ncurses** [?].
- **Bonus :** estimation de **validité** des données (fraîcheur) et
  **prédiction de fermeture**.
