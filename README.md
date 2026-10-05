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

Sans folio ni imposition, on tente la table des matières textuelle. Chaque
titre retrouvé plus loin dans le livre vote un décalage (`pdf_index − page
imprimée`). On n'accepte que si au moins trois voix concordent, dans l'ordre,
sur un offset qui laisse une page imprimée positive. La plage va du premier
titre retrouvé au dernier : le liminaire d'avant n'est pas interpolé. C'est
une mesure indirecte (confiance plafonnée à 50 %), à vérifier avant d'indexer,
et elle ne remplace jamais un folio ou une imposition déjà lus. À défaut de
concordance, on ne devine pas depuis la page de la table : on reste sur
l'identité.

### On chunke par section, jamais par fenêtre de tokens

Un stat block coupé en deux est inexploitable en retrieval. L'unité de base est
la feuille de l'arbre de sections ; on ne découpe plus finement qu'en dernier
recours. Chaque chunk est préfixé de son `section_path` **dans le texte
embeddé** — pas seulement en metadata.

Une section trop grande est divisée aux sous-titres internes en majuscules,
puis aux paragraphes et phrases si nécessaire. Le chevauchement vise 15 %
aux frontières de ce dernier découpage, ajusté aux unités entières qui tiennent
dans le budget. Le budget par défaut
est d'environ 1 200 tokens, estimés sans dépendance GPU, chemin compris ;
`--max-tokens` permet de l'ajuster. Le backend vérifie ensuite la vraie limite
du tokenizer : aucune troncature silencieuse. Une ligne indivisible qui dépasse
le budget est signalée. Chaque sous-chunk conserve ses folios page par page.

### Le retrieval est hybride, et ce n'est pas négociable

Un bot de règles doit retrouver des termes exacts que l'embedding dense écrase :
noms de compétences, de talents, valeurs numériques, jargon propre au système.
BM25 et dense en parallèle, fusion RRF, puis rerank. Les six places finales
sont réservées à des pages PDF distinctes : le chevauchement de chunks ne doit
pas évincer une deuxième preuve. Le passage le mieux classé de chaque page
reste la preuve fournie ; retrouver un folio ne garantit pas que ce passage
contient toutes les règles demandées.

Le score normalisé BGE est une [sigmoïde](https://bge-model.com/tutorial/5_Reranking/5.2.html),
pas une probabilité calibrée de justesse. Le seuil historique 0,5 rejetait des
preuves pertinentes. Nous calibrons donc **l’admission de la requête** sur un
[corpus original distinct](fixtures/retrieval-calibration.yaml), sans changer
les questions du manuel : puissance de dix maximisant la moyenne entre admission
des preuves positives et refus des négatifs, soit 0,1 (à égalité, seuil le
plus haut). Au-dessus de ce seuil pour le meilleur passage, les
preuves complémentaires restent candidates même avec un score inférieur.

Cette règle équilibre les deux classes : 15/19 preuves positives passent
le seuil et les 6/6 hors-corpus sont refusés. Le seuil 0,001, qui conserverait
tous les positifs, accepterait deux négatifs ; nous ne le retenons pas. Il ne prouve ni la pertinence
de chaque contexte secondaire, ni la justesse d’une réponse. Le seuil reste
surchargeable et devra être validé sur plusieurs livres ; le refus final exige
une vérification des preuves par l’agent, encore non validée sur le jeu complet.

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

## Démo locale : PDF → question → page imprimée

```bash
uv sync --extra index
uv run --extra index python scripts/make_demo_pdf.py /tmp/rulelawyer-demo/veilleurs.pdf
uv run --extra index rulelawyer ask /tmp/rulelawyer-demo/veilleurs.pdf \
  "Combien coûte la traversée d'un Pont de brume ?" \
  --cache-dir /tmp/rulelawyer-demo/index --debug
uv run --extra index rulelawyer ask /tmp/rulelawyer-demo/veilleurs.pdf \
  "Quel dé faut-il lancer pour attaquer un dragon ?" \
  --cache-dir /tmp/rulelawyer-demo/index
```

Avec `OPENROUTER_API_KEY` définie, résultat attendu : **3 étincelles (p. 42)**.
Sans clé, la première commande retrouve `book_page=42` puis s'arrête avant
l'appel de génération avec une erreur explicite. La question hors livre
renvoie **« Ce n'est pas dans le manuel. »**, sans clé ni appel de génération.
Le PDF original de démonstration est sous CC0 : quatre pages
physiques, folios 41–44 et deux règles inventées. Aucun livre n'est téléchargé.

La première recherche télécharge BGE-M3 et le reranker BGE v2 m3 (plusieurs Go).
Le périphérique est choisi automatiquement : MPS sur Mac compatible, CUDA
si disponible, sinon CPU. Aucune API d'embedding. Qdrant persiste dans
`--cache-dir`, sans serveur ni Docker.
BM25 et dense récupèrent chacun jusqu'à 30 sections, fusion RRF (k=60), puis
reranking des passages paginés vers six pages distinctes au maximum.
`--threshold` fixe le seuil d’admission de la requête (0,1 par défaut,
calibré sur un corpus original distinct ; à valider sur d’autres manuels).

La réponse utilise **OpenRouter, `openai/gpt-4o-mini`, température 0**, via
`https://openrouter.ai/api/v1/chat/completions` et `OPENROUTER_API_KEY`.
La clé n’est ni enregistrée ni affichée. Cette démo utilise le client
HTTP standard Python ; l’intégration SDK Anthropic est disponible séparément
avec `--provider anthropic` et l’extra `[agent]`. Aucun recours à Claude CLI ou à un modèle `:free`.

Le retrieval fixe le passage et sa `book_page` **avant** la génération.
Seul le texte de ce passage est envoyé au modèle, qui ne choisit jamais de
page. Cette version répond par phrases complètes reprises de la preuve : le
code les vérifie et ajoute le folio du passage. Une valeur inventée ou un
numéro de page étranger au passage produit « Ce n'est pas établi par le
manuel. ». Une panne OpenRouter est une erreur explicite. Sans résultat
au-dessus du seuil, le refus hors livre ne contient aucun numéro de page.

Pour produire seulement le format commun, sans dépendance d'indexation :

```bash
uv run rulelawyer ingest mon-livre.pdf --output .cache/mon-livre
```

Le dossier contient `probe.json`, `chunks.jsonl` et `stats.json`. Les statistiques
comptent les sections, chunks, tailles estimées par tranches et occurrences
d'images retenues/filtrées. Le filtre mesure les pixels source (100×100 minimum)
et les octets du flux PDF encodé (5 Ko minimum), pas la taille affichée.
« Retenue » signifie éligible : le captioning et l'indexation d'images restent
à valider. Ce filtre ne sait pas distinguer une texture d'une illustration utile.
Sur le manuel local : 150 sections, 344 chunks, min/médiane/max
17/992/1 200 tokens ; tranches ≤300 / 301–600 / 601–900 / 901–1 200 :
40 / 42 / 58 / 204. Images : 369 occurrences, 362 éligibles, 7 filtrées,
0 indexée. Ces comptes n'impliquent aucune mesure d'utilité mécanique.

Les sections conservent le
texte et le folio de chaque page, et leur chemin est inclus dans le texte
embeddé. Une section multifeuille garde donc des citations exactes. Une
destination de section ambiguë est signalée et peut être précisée par un
profil. Les signets hors ordre sont replacés selon leurs ancres, en conservant
leur hiérarchie, et l'extraction suit l'ordre du flux texte du PDF pour éviter
de fusionner deux colonnes. Routes B–D, images et mises en page dont le flux
texte est incorrect restent à traiter.

## Agent Anthropic et REPL

```bash
uv sync --extra index --extra agent
uv run --extra index --extra agent rulelawyer ask mon-livre.pdf \
  "Ma question de règles" --provider anthropic --debug
uv run --extra index --extra agent rulelawyer repl mon-livre.pdf --debug
# Validation de génération réelle : clé requise, aucun appel sans elle.
uv run --extra index --extra agent python eval/run_eval.py \
  --pdf "$PWD/Corporation-RPG-Core-Rulebook.pdf" --generate --provider anthropic
```

Le REPL utilise Anthropic par défaut ; `/history`, `/clear` et `/quit`
consultent, effacent et terminent la session. L’index reste ouvert et
l’historique reste en mémoire, sans fichier de conversation. Les douze derniers
échanges sont transmis à l’agent Anthropic ; le retrieval utilise la question
courante. Les questions doivent donc nommer leur sujet : la résolution des
références implicites avant la recherche reste à améliorer. `--debug` affiche
les passages et leurs scores. `ask` conserve OpenRouter par défaut ;
`--provider openrouter` est aussi disponible dans le REPL.

Le [SDK officiel Anthropic](https://platform.claude.com/docs/en/api/sdks/python)
utilise `ANTHROPIC_API_KEY`, `claude-sonnet-4-6` et une température de zéro.
Cette voie reçoit jusqu’à six preuves ; le modèle associe chaque affirmation
à un indice de source et une citation textuelle. Le code contrôle ces sources,
les citations textuelles et les valeurs numériques, puis ajoute les folios
mesurés. Le modèle doit répondre dans la langue de la question, garder les
termes du livre, demander une clarification si nécessaire, ou refuser malgré
du contexte insuffisant. Les refus français/anglais sont limités à des textes
connus, avec la section la plus proche lorsqu’elle existe. La voie OpenRouter
historique reste limitée au premier passage et à des phrases extractives.

Ce contrat permet les réponses croisées sans laisser choisir une page librement
au modèle. Il ne prouve pas qu’une paraphrase est sémantiquement fidèle :
une citation exacte peut accompagner une interprétation fausse, et les nombres
écrits en lettres ne sont pas vérifiés par le garde numérique. Les citations
textuelles de moins de vingt caractères sont refusées ; les cas courts doivent
fournir leur phrase de contexte. La justesse, la langue des réponses et la
qualité des clarifications doivent être validées sur le jeu complet.

**Point 5 partiel** : SDK réel testé sur transport HTTP simulé, REPL et
validation multi-preuves testés sur des règles originales. Aucun appel LLM
réel dans cette reprise. Les clés Anthropic et OpenRouter sont absentes :
les deux runners de génération s’arrêtent avant l’indexation et le réseau.
La validation des 30 questions en génération reste bloquée ; les points
6 à 9 attendent cette étape, conformément à l’ordre du brief.

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
rulelawyer ingest mon-livre.pdf --profile profiles/mon-jdr.yaml
```

Le matching automatique consulte `profiles/` (ou `--profiles-dir`) et exige
tous les critères du YAML. Plusieurs profils compatibles provoquent une
erreur : `--profile` permet un choix explicite, toujours vérifié contre le PDF.
Le rapport indique le profil appliqué. Voir les
[paramètres et conventions](profiles/README.md), notamment la pagination et
les ancres de titres répétées. Le profil fourni pour le manuel de référence
contient uniquement des empreintes et paramètres, aucun passage du livre.

## Évaluation

Le [jeu de 30 questions](eval/questions.yaml) est lié au hash du manuel local
de référence et couvre faits, procédures, questions croisées, hors-livre et
ambiguïtés. Les pages attendues sont les folios du livre, jamais les index PDF.

```bash
uv run --extra index python eval/run_eval.py \
  --pdf /chemin/Corporation-RPG-Core-Rulebook.pdf
```

Ce mode mesure le retrieval avec les vrais modèles locaux. `--generate`
active aussi les appels OpenRouter (clé requise) en envoyant les passages
retrouvés. Sans cette option, les métriques de génération restent nulles.
Les chunks, l'index et les rapports restent locaux et ignorés par Git.
Voir [les métriques et limites](eval/README.md) pour interpréter le résultat.
La [baseline réelle sur Corporation](eval/baseline.md) était à **60,42 %**.
La [correction du retrieval](eval/retrieval-improvement.md) atteint **89,58 %
de Recall@6**, au-dessus de la cible de 85 %, avec les mêmes questions et
chunks. Le refus du retrieval reste à 2/3 hors-livre ; la génération n’est
pas validée sur ce jeu.

## État

**Disponible aujourd'hui : `probe`, `ingest` Route A et `ask` sur PDF natif
avec outline exploitable, REPL et SDK Anthropic.** La génération Anthropic
sur le manuel reste non validée. La démo ci-dessus traverse extraction, index local,
retrieval hybride et génération OpenRouter conditionnée à la présence de la
clé. Le chargement de profils YAML et `rulelawyer profile init` sont disponibles.

- [x] Scaffold, hygiène du dépôt et CI Linux / macOS / Windows : hook
  pre-commit, refus des contenus interdits et des fichiers de secrets,
  lint, formatage, types et tests.
- [x] Socle du probe et CLI : analyse de la couche texte, de l'outline,
  des colonnes, du boilerplate, des folios, des images et de la table des
  matières ; recommandation de route et rapport JSON.
- [x] Repli du mapping de pagination via la table des matières lorsque les
  folios manquent.
- [x] Fixture redistribuable sans outline : `fixtures/knave.pdf`
  (Knave 1.0, CC BY 4.0). PDF natif, outline absent, route B.
- [ ] Validation sur d'autres PDF réels, notamment un scan sans couche
  texte (Route C).
- [x] Route A : extraction depuis l'outline, découpage borné des sections
  longues et provenance page par page ; validation synthétique et manuel local.
- [x] Profils : chargement YAML, matching, surcharges et `profile init`.
- [x] Index Qdrant local, BM25 + dense BGE-M3, RRF, reranker et seuil de refus.
- [x] CLI `ingest` / `ask`, page choisie par retrieval et garde OpenRouter sans clé.
- [x] Validation réelle de la génération OpenRouter avec une clé configurée : démo Veilleurs, « Traverser un Pont de brume coûte exactement 3 étincelles. (p. 42) ».
- [x] Jeu de 30 questions et runner d'évaluation du retrieval, avec métriques
  optionnelles de génération, refus et citations.
- [x] Recall@6 ≥ 85 % sur le manuel de référence : 89,58 %
  (baseline : 60,42 %), questions inchangées, seuil calibré hors du manuel.
- [ ] Validation de la génération sur le jeu complet, clarification des questions
  ambiguës et réponses croisées sur plusieurs passages.
- [x] REPL et intégration SDK Anthropic : tests de contrat avec transport
  simulé ; aucune validation LLM réelle sur le manuel dans cette reprise.
- [ ] Route B, serveur MCP, Space Hugging Face, Routes C et D.

Les tests synthétiques vérifient notamment la pagination différente du PDF, le repli par la table des matières,
les sections multifeuilles, le retrieval et la réouverture de Qdrant, le refus
hors livre, l'arrêt avant réseau sans clé et le rejet de citations inventées.
Les modèles et le transport OpenRouter sont doublés dans les tests automatisés.
Le vrai SDK Anthropic utilise un transport HTTP simulé ; les tests couvrent
plusieurs preuves, refus, clarification, valeurs/citations invalides, erreurs
fournisseur, troncature et absence de clé. La justesse sémantique reste non validée.
Le retrieval a été exécuté avec les vrais modèles locaux. L'appel OpenRouter
a été validé sur la démo Veilleurs : 3 étincelles, page 42. Les tests du PDF commercial sont optionnels.

```bash
uv sync --group test-index
uv run --group test-index ruff check .
uv run --group test-index ruff format --check .
uv run --group test-index mypy
uv run --group test-index pytest -q
```

Le groupe `test-index` permet de tester Qdrant, BM25 et le contrat SDK
Anthropic en CI sans installer torch. La CI multi-OS rejoue ces vérifications à chaque push sur `main`.
