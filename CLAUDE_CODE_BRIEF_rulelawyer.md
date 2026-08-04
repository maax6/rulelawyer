# BRIEF — `rulelawyer`

Assistant de règles pour jeux de rôle papier. Tu lui donnes un PDF de livre de règles, il te répond en citant les pages.

Open source, MIT. **Le projet ne contient et ne distribue aucun contenu de livre.** L'utilisateur apporte son propre PDF, légalement acquis.

*(Nom à valider — `rulelawyer` est la private joke TTRPG de l'archétype du joueur qui connaît le bouquin par cœur. Renomme si tu as mieux.)*

Tu es en autonomie. Lis ce brief en entier, puis propose-moi un plan avant d'écrire du code.

---

## 0. Le pivot par rapport à un RAG mono-document

L'erreur à éviter absolument : écrire le pipeline pour **un** livre puis le « généraliser » après coup. Les livres de JDR varient énormément :

| Dimension | Extrêmes rencontrés |
|---|---|
| Couche texte | PDF natif propre ↔ scan pur sans OCR |
| Outline | 160 entrées sur 2 niveaux ↔ aucune |
| Colonnes | 1 ↔ 3, parfois variable dans le même livre |
| Fonds | blanc ↔ texture parchemin pleine page qui casse l'OCR |
| Densité visuelle | quasi texte ↔ mise en page où le layout *porte* du sens |
| Pagination | index PDF = page livre ↔ décalage variable (couvertures, encarts) |

Donc : **le pipeline commence par un diagnostic, et le diagnostic décide de la route d'ingestion.** Écris la phase de probe en premier, avant tout le reste.

**Le contrat de sortie est unique et stable** : quelle que soit la route, on produit le même `chunks.jsonl` avec le même schéma. Tout ce qui est en aval (index, retrieval, agent) est agnostique de la route.

---

## 1. Cas de test de référence (local, non versionné)

J'ai un PDF sur lequel j'ai déjà fait le diagnostic. Sers-t'en comme fixture de dev, mais **il ne rentre jamais dans le repo** :

- 257 pages, PDF natif (polices Type 1C embarquées, aucun OCR nécessaire)
- Outline de 160 entrées sur 2 niveaux, propre
- Ordre de lecture correct via extraction simple, y compris en double colonne
- 369 images embarquées, dont une majorité d'éléments de maquette à filtrer
- Boilerplate d'imposition répété sur 256/257 pages, contenant le numéro de page imprimé
- Décalage : index PDF 0-based = page livre − 1

C'est le cas **le plus facile** (Route A ci-dessous). Ne calibre pas le projet dessus. Trouve-toi au moins deux autres PDF de test : un sans outline, et un scanné. Les quickstarts gratuits sur DriveThruRPG et les JDR sous Creative Commons / OGL sont parfaits pour ça et sont redistribuables — si tu en trouves un, **lui** peut aller dans le repo comme fixture de test.

---

## 2. Phase 1 — Le probe (`src/rulelawyer/probe.py`)

Prend un PDF, sort un `ProbeReport` en JSON. Aucune décision d'ingestion n'est prise ailleurs.

Ce qu'il doit détecter :

1. **Couche texte** — `pdffonts` : polices listées ou non. Si aucune → scan.
   Attention au cas piège : certains PDF ont une couche texte OCR de mauvaise qualité déjà présente. Mesure la vraisemblance (taux de mots dans un dictionnaire, ratio de caractères non imprimables) et signale-la ; une mauvaise couche texte est pire que pas de couche du tout.
2. **Outline** — nombre d'entrées, profondeur max, couverture (les plages de pages couvrent-elles le document sans trou ?). Un outline de 4 entrées sur 300 pages est inutilisable, traite-le comme absent.
3. **Colonnes** — clustering des coordonnées x des blocs de texte sur un échantillon de 20 pages. Sors le nombre de colonnes dominant et la variance.
4. **Boilerplate** — détection **générique** : normalise et hashe chaque ligne, garde celles qui apparaissent sur plus de 60 % des pages. Ne code jamais un regex en dur pour un livre donné.
5. **Numéro de page imprimé** — cherche un entier isolé dans les zones d'en-tête/pied. S'il est trouvé et monotone, tu as ton mapping `pdf_index → book_page` **mesuré** plutôt que deviné. Sinon, fallback sur un offset détecté via la table des matières.
6. **Images** — nombre, distribution des tailles. Le ratio « grandes images / pages » indique si le livre est illustration-lourd.
7. **Table des matières textuelle** — repère les pages dont la structure est `titre .... numéro`. C'est la source de secours quand l'outline manque.

Sortie : un rapport lisible en CLI (`rulelawyer probe mon-livre.pdf`) **et** un JSON. L'utilisateur doit pouvoir comprendre pourquoi son livre va prendre 2 minutes sur CPU ou 40 minutes sur GPU.

---

## 3. Phase 2 — Les routes d'ingestion

Le probe choisit, l'utilisateur peut forcer avec `--route`.

### Route A — natif + outline exploitable
**CPU seul, pas de modèle, quelques minutes.** C'est la route rapide, et elle couvre une grosse part des JDR commerciaux modernes.

- Sections = outline. C'est la vérité terrain, ne la re-détecte pas.
- Texte via `pdfplumber` (garde les bbox).
- **N'essaie pas de détecter les titres par heuristique de police sur cette route** — les livres de JDR utilisent des polices décoratives qui font échouer toute heuristique. L'outline existe, sers-t'en.

### Route B — natif, outline absent ou inutilisable
**CPU seul, plus lent.** Il faut reconstruire la hiérarchie :

1. Source primaire : **la table des matières textuelle** repérée par le probe. Parse-la, résous les numéros de page vers des index PDF, tu as ton arbre.
2. Source secondaire : clustering des styles de caractères (`pdfplumber` expose `fontname` + `size` par char). Les titres forment des clusters distincts et peu nombreux. Attribue un niveau par taille décroissante.
3. Croise les deux. Si elles divergent fortement, signale-le et demande confirmation avant d'indexer — mieux vaut un warning qu'un index silencieusement faux.

### Route C — scan, ou couche texte pourrie
**GPU requis.** C'est ici qu'on sort l'artillerie VLM :

- Défaut : **MinerU 2.5** (`opendatalab`). Il sort un `content_list.json` avec `type` / `page_idx` / `bbox` par bloc, ce qui alimente directement notre chunking. Il gère les multi-colonnes et lie les images à leurs captions.
- Alternative sélectionnable : **PaddleOCR-VL** (`PaddlePaddle`), meilleur sur certains layouts exotiques et multilingue.
- **N'utilise pas dots.ocr** : il ne parse pas les images des documents, ce qui est disqualifiant pour du JDR.
- Cette route s'exécute derrière une interface `Parser` — le reste du pipeline ne doit pas savoir quel modèle a tourné.
- Isole les deps GPU dans un extra `uv` optionnel (`rulelawyer[vlm]`). Un utilisateur en Route A ne doit pas télécharger torch.

### Route D — retrieval visuel (expérimental, `--route visual`)
Pour les livres où le layout porte du sens et où le parsing détruit l'information. Embedding direct des **images de pages** via la famille **ColPali / ColQwen** (`vidore`), pas de parsing du tout.

Coût : index multi-vecteurs bien plus lourd, et pas de texte source à citer — on cite la page et on l'affiche en image. Implémente-la en dernier, marque-la expérimentale, et documente honnêtement le compromis.

---

## 4. Les profils (`profiles/*.yaml`) — le mécanisme communautaire

C'est le cœur de la valeur open source du projet, réfléchis-y bien.

Un profil est un YAML qui **surcharge** l'auto-détection pour un livre précis. Il contient des paramètres, jamais du contenu :

```yaml
name: "Exemple RPG — Core Rulebook (1st ed.)"
match:
  page_count: 257
  producer_contains: "pdf-tools.com"
  first_page_text_sha256: "..."     # empreinte, pas le texte
route: A
page_offset: -1
boilerplate_patterns:
  - '\w+ \d{4} \w+:Layout.*?Page \d+'
drop_sections: ["Front Cover", "Credits", "Index"]
table_pages: [57, 58, 141]
notes: "Les tables de véhicules p.57 sortent mieux en mode -layout."
```

Pourquoi c'est bien :

- **Un profil ne contient aucune œuvre protégée.** Il est donc librement partageable, contrairement à un index.
- La communauté contribue des profils par PR. Chaque nouveau JDR supporté est un fichier de 20 lignes, pas du code.
- Le matching est automatique : au probe, si un profil correspond, on l'applique et on le dit à l'utilisateur.

Prévois `rulelawyer profile init <pdf>` qui génère un squelette pré-rempli depuis le probe, que l'utilisateur ajuste puis soumet en PR.

---

## 5. Chunking (identique quelle que soit la route)

**Règle d'or : on chunke par section, jamais par fenêtre de tokens fixe.** Une entrée d'arme ou un stat block coupé en deux est inexploitable en retrieval.

1. Unité de base = feuille de l'arbre de sections.
2. Si > ~1200 tokens, découpe sur les sous-titres internes (lignes entièrement en majuscules — très régulier en JDR).
3. Si ça dépasse encore, découpe aux paragraphes avec 15 % de chevauchement, en dernier recours.
4. **Chaque chunk est préfixé de son `section_path`** dans le texte embeddé, pas seulement en metadata. Gain net de rappel sur les questions contextuelles.

Schéma :

```python
{
    "id": str,
    "book_id": str,  # slug du profil ou hash du PDF
    "text": str,  # préfixé du section_path
    "raw_text": str,
    "section_path": str,  # "Section 02: Equipment > Tactical Firearms"
    "book_page": int,  # page IMPRIMÉE, celle que l'utilisateur cherche
    "pdf_page": int,
    "type": "rules" | "table" | "image_caption",
    "images": [str],
    "route": "A" | "B" | "C" | "D",
}
```

`book_page` vs `pdf_page` : ne les confonds jamais. On cite toujours `book_page`, c'est celle imprimée dans le livre de l'utilisateur.

### Images

- Filtre le bruit : ignore < 100×100 px ou < 5 KB (filets, textures, puces).
- Captioning derrière une interface `Captioner` : API Anthropic (`claude-sonnet-4-6`) par défaut, `Qwen/Qwen3-VL-4B-Instruct` en local pour l'option zéro-appel-externe.
- Cache sur disque indexé par hash d'image. Le captioning ne tourne qu'une fois.
- **Mesure l'utilité** : si moins de 15 % des captions contiennent de l'information mécanique (et sur du JDR, beaucoup d'illustrations sont purement d'ambiance), désactive l'indexation des images par défaut et ne les sers qu'en illustration d'une réponse.

---

## 6. Index et retrieval

**Embeddings** : `BAAI/bge-m3`. Multilingue — non négociable, on pose des questions en français sur des livres en anglais. Produit dense + sparse en une passe. Benchmark `Qwen/Qwen3-Embedding-0.6B` en alternative et garde le meilleur sur l'éval.

**Store** : Qdrant en Docker. Une collection, filtrage par `book_id` — plusieurs livres coexistent, et l'agent peut chercher dans un seul ou tous.

**Retrieval hybride** — c'est le point le plus important du projet. Un bot de règles doit retrouver des termes exacts que l'embedding dense écrase : les noms de compétences, de talents, les valeurs numériques, les termes propres au système.

1. BM25 → top 30
2. Dense → top 30
3. Fusion RRF (k=60)
4. Rerank `BAAI/bge-reranker-v2-m3` → top 6
5. Filtres optionnels : `book_id`, `section_path`
6. Seuil de score en dessous duquel on déclare « rien trouvé »

---

## 7. L'agent

**Prompt système**, contraintes à encoder :

- Répond dans la langue de la question, mais **les termes de jeu restent dans la langue du livre**. Ne traduis jamais un nom de compétence, sinon l'utilisateur ne le retrouve pas dans son exemplaire.
- Chaque affirmation mécanique est suivie de sa page : `(p. 39)`.
- Si le contexte ne permet pas de répondre : le dire, et proposer la section la plus proche.
- **Interdiction absolue de combler avec de la connaissance générale de JDR.** C'est le risque numéro un de ce projet : le modèle connaît D&D et Pathfinder, et va produire des réponses plausibles mais fausses sur un système obscur. Une réponse plausible et fausse est pire qu'un refus. Le prompt doit le marteler, et l'éval doit le mesurer.

**Interfaces**, par ordre de priorité :

1. **CLI** — REPL avec `rich`, historique, `--debug` qui affiche les chunks récupérés et leurs scores. Mode de dev et de démo.
2. **Serveur MCP** — tools `search_rules(query, book_id?, section?)` et `get_section(book_id, section_path)`. C'est la sortie la plus réutilisable : n'importe quel client MCP devient un bot de règles sans écrire d'interface de chat.
3. **Space Hugging Face** — l'utilisateur upload son PDF, traitement **éphémère**, rien de persisté, purge en fin de session. C'est la vitrine. Écris-le clairement dans le README du Space.
4. **Bot Discord** (optionnel) — `/regle <question>`, réponse en embed, pages en footer.

---

## 8. Hygiène open source — à faire dès le premier commit

- `.gitignore` **strict** sur `data/`, `*.pdf`, `*.jsonl`, `qdrant_storage/`, `.cache/`. Ajoute un hook pre-commit qui refuse tout fichier > 1 MB et tout `.pdf`. Je ne veux pas pouvoir pusher un corpus par accident, y compris depuis un notebook.
- Licence MIT. Pas de contenu tiers dans le repo, hors fixtures explicitement redistribuables (OGL / Creative Commons), documentées dans `fixtures/SOURCES.md`.
- **README** : une section « Legal » en tête, courte et non défensive — *cet outil ne contient aucun livre de règles ; il indexe un PDF que vous possédez, en local ; n'en partagez pas l'index.*
- Un `CONTRIBUTING.md` centré sur les profils : c'est là qu'on veut les contributions, et il faut que ce soit évident en 30 secondes.
- CI GitHub Actions : lint, types, tests, et un test qui vérifie qu'aucun fichier interdit n'est tracké.

---

## 9. Évaluation

Sans ça le projet n'est pas terminé. `eval/questions.yaml`, **30 questions minimum** sur le livre de référence :

- 10 factuelles précises
- 8 procédurales
- 6 nécessitant de croiser deux sections
- 3 hors-livre → la bonne réponse est « ce n'est pas dans le manuel »
- 3 ambiguës → la bonne réponse est une demande de clarification

Chaque entrée : `question`, `expected_pages`, `must_contain`, `must_not_hallucinate`.

Métriques (`eval/run_eval.py`) :

- **Recall@6** sur `expected_pages` — la métrique qui compte
- Taux de refus correct sur les hors-livre
- Taux de citation de page présente **et** exacte

Le jeu d'éval ne contient que des questions et des numéros de page, pas de texte du livre — il est donc publiable, et il sert de preuve que l'outil marche.

---

## 10. Ordre d'exécution

1. Scaffold, `pyproject.toml` (avec extras `[vlm]`), `docker-compose.yml`, `.gitignore` + pre-commit. **Stop, montre-moi.**
2. **Probe** seul, avec sortie CLI lisible. Fais-le tourner sur mes PDF de test et montre-moi les rapports. **Stop.**
3. Route A complète + chunking + profils. Stats de sortie : sections, chunks, distribution des tailles, images retenues/filtrées. **Stop.**
4. Index + retrieval hybride + les 30 questions. **Fais tourner l'éval avant d'écrire l'agent** — si le Recall@6 est sous 0.85, on corrige le chunking, pas le prompt.
5. Agent + CLI.
6. Route B.
7. Serveur MCP.
8. Space HF.
9. Route C (VLM), puis D (visuel) si tout le reste est vert.

---

## 11. Ce que je ne veux pas voir

- Un `RecursiveCharacterTextSplitter` sur le texte brut. Ce brief existe pour éviter exactement ça.
- LangChain pour l'orchestration. Le pipeline fait 200 lignes à la main et tu contrôles chaque étape.
- Une décision d'ingestion prise ailleurs que dans le probe.
- Un `try/except` qui avale une erreur d'extraction. Si une page échoue, je veux le savoir bruyamment.
- Torch installé par défaut pour quelqu'un qui n'a besoin que de la Route A.
- Un README qui décrit ce que le code fait. Écris plutôt « décisions et arbitrages » : pourquoi le routage, pourquoi le chunking section-aware, pourquoi le retrieval hybride.
