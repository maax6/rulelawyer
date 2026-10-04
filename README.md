# rulelawyer

Assistant de règles pour jeux de rôle papier. Vous lui donnez le PDF de votre
livre, il répond à vos questions **en citant les pages imprimées**.

> *rulelawyer* : l'archétype du joueur qui connaît le bouquin par cœur et
> l'ouvre à la bonne page au milieu d'une scène. C'est le cahier des charges.

https://github.com/user-attachments/assets/e586d27b-ec01-43c4-af1e-7a0878a90763

---

## Legal

Cet outil **ne contient et ne distribue aucun livre de règles**. Il indexe, en
local, un PDF que vous possédez. L'index produit est un dérivé de l'œuvre :
gardez-le pour vous, ne le partagez pas.

Le dépôt refuse mécaniquement toute contribution de contenu : un hook
pre-commit rejette les PDF et tout fichier de plus de 1 Mio. Les seules fixtures
acceptées sont explicitement redistribuables (OGL, Creative Commons) et
documentées dans [`fixtures/SOURCES.md`](fixtures/SOURCES.md).

Code sous licence MIT.

---

## Décisions et arbitrages

Ce README explique *pourquoi* le pipeline est fait comme ça. Ce qu'il fait se
lit dans le code.

### Le diagnostic passe avant l'ingestion

L'erreur naturelle est d'écrire le pipeline pour un livre, puis de le
« généraliser ». Les livres de JDR ne s'y prêtent pas : entre un PDF natif
propre avec 160 entrées d'outline et un scan pleine page sur fond parchemin, il
n'y a pas un paramètre à ajuster, il y a deux pipelines différents.

Donc `rulelawyer probe` est écrit en premier et **décide seul** de la route
d'ingestion. Tout ce qui suit — chunking, index, agent — consomme le rapport
sans jamais re-diagnostiquer, et produit le même `chunks.jsonl` quelle que soit
la route empruntée.

```bash
rulelawyer probe mon-livre.pdf
```

| Route | Quand | Coût |
|---|---|---|
| **A** | natif + outline exploitable | CPU, quelques minutes |
| **B** | natif, hiérarchie à reconstruire | CPU, plus lent |
| **C** | scan, ou couche texte pourrie | GPU (VLM) |
| **D** | le layout porte le sens (expérimental) | GPU, index lourd |

### Une mauvaise couche texte est pire que pas de couche du tout

Un PDF sans texte est un cas clair : on sort le VLM. Un PDF avec une couche OCR
médiocre est le vrai piège — l'extraction « marche », l'index se construit, et
les réponses sont fausses. Le probe mesure donc la *vraisemblance* du texte
(caractères de remplacement, mots hachés, polices embarquées ou non) et sait
répondre « il y a du texte, jette-le ».

Ces signaux sont volontairement indépendants de la langue. Un dictionnaire
système ne couvre que l'anglais ; s'en servir comme critère principal
classerait tout livre français comme du mauvais OCR.

### Le boilerplate se détecte par fréquence, jamais par regex

Aucune expression régulière propre à un livre n'entre dans le code. On normalise
chaque ligne, on **masque les suites de chiffres**, on hache, et on garde ce qui
apparaît sur plus de 60 % des pages.

Le masquage n'est pas un détail : sur beaucoup de livres, la ligne d'imposition
répétée *contient le numéro de page*. Sans masquage, chaque occurrence est
unique, la détection ne trouve rien — et se présente comme « ce livre n'a pas de
boilerplate », ce qui est faux et silencieux.

Corollaire : les numéros de page imprimés sont lus **avant** tout retrait de
boilerplate, sans quoi on détruit la seule mesure directe de la pagination.

### `book_page` n'est pas `pdf_page`, et le décalage n'est pas un scalaire

On cite toujours la page **imprimée dans le livre** : c'est celle que le joueur
cherche. Le probe la mesure en cherchant les entiers des zones d'en-tête et de
pied, et retient le décalage le mieux soutenu — page par page.

Le résultat est une liste de plages, pas un nombre. Couvertures et encarts
introduisent des décalages successifs, et un offset unique produirait des
citations fausses sur une partie du livre. Le `page_offset` scalaire des profils
est une surcharge utilisateur pour le cas dégénéré, pas la représentation
interne.

Le folio prime sur tout autre nombre. Beaucoup de livres portent en en-tête une
marque d'imposition du type `…Layout 1 02/04/2009 Page 38` : c'est un artefact
de fabrication qui compte les feuilles du fichier de maquette. Elle est présente
sur *plus* de pages que le folio, parfaitement régulière, et coïncide parfois
avec la pagination réelle — par chance, jamais par construction. Un vote au
volume la choisirait, à pleine confiance, et décalerait chaque citation. On ne
retient donc que les lignes dont le texte entier est un nombre ; l'imposition
sert de repli, en le disant, et un désaccord entre les deux fait chuter la
confiance au lieu d'être tranché en silence.

Et une page dont le folio n'a pas pu être lu ne reçoit pas de numéro plausible :
`book_page()` y répond `None`. Seules les pages sans folio *encadrées* par un
décalage identique des deux côtés sont interpolées, et le rapport distingue
alors ce qui est mesuré de ce qui est déduit.

### On chunke par section, jamais par fenêtre de tokens

Un stat block coupé en deux est inexploitable en retrieval. L'unité de base est
la feuille de l'arbre de sections ; on ne découpe plus finement qu'en dernier
recours. Chaque chunk est préfixé de son `section_path` **dans le texte
embeddé** — pas seulement en metadata.

### Le retrieval est hybride, et ce n'est pas négociable

Un bot de règles doit retrouver des termes exacts que l'embedding dense écrase :
noms de compétences, de talents, valeurs numériques, jargon propre au système.
BM25 et dense en parallèle, fusion RRF, rerank, seuil en dessous duquel on
répond « rien trouvé ».

### Le risque numéro un est la réponse plausible et fausse

Le modèle connaît D&D et Pathfinder. Sur un système obscur, il produira des
réponses crédibles et inventées. L'agent a interdiction de compléter avec de la
connaissance générale de JDR, et l'éval le mesure : sur les questions hors-livre,
la bonne réponse est « ce n'est pas dans le manuel ».

### Pas de LangChain, pas de torch par défaut

Le pipeline tient en quelques centaines de lignes lisibles. Les dépendances GPU
vivent dans des extras (`[vlm]`, `[index]`, `[visual]`) : qui n'a besoin que de
la Route A ne télécharge pas torch.

---

## Installation

```bash
uv sync
```

Pour l'ingestion VLM (Route C, GPU) :

```bash
uv sync --extra vlm
```

## Setup développeur

Le hook qui protège le dépôt s'active en une ligne, la même sur Linux, macOS et
Windows :

```bash
git config core.hooksPath .githooks
```

Sur Windows, Git fournit son propre `sh` : le hook est un unique script POSIX,
il n'y a pas de variante `.bat` à maintenir en parallèle. Deux précautions y
sont prises explicitement — `.gitattributes` force le hook en LF (en CRLF, `sh`
échoue sur `bad interpreter: /bin/sh^M`) et le hook résout l'interpréteur parmi
`py -3`, `python3` et `python`, parce que `python3` n'existe pas comme commande
sur Windows. La CI exécute la suite sur les trois systèmes.

## Profils

Un profil est un YAML de vingt lignes qui surcharge l'auto-détection pour un
livre précis. **Il ne contient aucune œuvre protégée** — que des paramètres et
des empreintes — donc il est librement partageable, contrairement à un index.

C'est là qu'on attend les contributions : voir
[`CONTRIBUTING.md`](CONTRIBUTING.md).

```bash
rulelawyer profile init mon-livre.pdf > profiles/mon-jdr.yaml
```

## État

**Disponible aujourd'hui : le diagnostic d'un PDF.** La CLI expose `probe`
(rapport lisible et export JSON avec `--json`) et `version`. Elle recommande
une route, mais n'ingère pas encore le livre et ne répond pas aux questions.
Les sections ci-dessus sur le chunking, le retrieval, l'agent et les profils
décrivent la cible ; `rulelawyer profile init` n'est pas encore disponible.

- [x] Scaffold, hygiène du dépôt et CI Linux / macOS / Windows : hook
  pre-commit, refus des contenus interdits et des fichiers de secrets,
  lint, formatage, types et tests.
- [x] Socle du probe et CLI : analyse de la couche texte, de l'outline,
  des colonnes, du boilerplate, des folios, des images et de la table des
  matières ; recommandation de route et rapport JSON.
- [ ] Terminer la phase 1 : repli du mapping de pagination via la table des
  matières lorsque les folios manquent ; validation sur d'autres PDF réels,
  notamment sans outline et scannés. Aucune fixture redistribuable n'est
  encore ajoutée dans `fixtures/`.
- [ ] Route A : extraction structurée depuis l'outline et chunking par section.
- [ ] Profils : chargement YAML, matching, surcharges et `profile init`.
- [ ] Index, retrieval hybride et jeu d'évaluation des réponses.
- [ ] Agent de questions-réponses et CLI d'ingestion / conversation.
- [ ] Route B, serveur MCP, Space Hugging Face, Routes C et D.

Validation de cet état : **62 tests passent en local**, dont les 12 tests
d'acceptation sur le PDF de référence non versionné. La
[CI du commit `f05b27a`](https://github.com/maax6/rulelawyer/actions/runs/37216311048)
est verte sur les trois systèmes ; elle n'exécute pas les 12 tests nécessitant
ce PDF local.
