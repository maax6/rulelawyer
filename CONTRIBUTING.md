# Contribuer

## Ce qu'on attend en priorité : des profils

Un **profil** est un fichier YAML d'une vingtaine de lignes qui surcharge
l'auto-détection pour un livre précis. C'est là que se joue la valeur du projet :
chaque nouveau JDR supporté est un fichier de config, pas du code.

Un profil ne contient **aucune œuvre protégée** — que des paramètres et des
empreintes. Il est donc librement partageable, contrairement à un index.

```bash
rulelawyer probe mon-livre.pdf                       # regarde ce qui est mal détecté
rulelawyer profile init mon-livre.pdf > profiles/mon-jdr.yaml
```

Ajustez, vérifiez que le probe donne le bon résultat, ouvrez une PR avec le
YAML seul.

```yaml
name: "Mon JDR — Core Rulebook (1re éd.)"
match:
  page_count: 257
  producer_contains: "pdf-tools.com"
  first_page_text_sha256: "..."      # empreinte de la première page PORTEUSE
                                     # de texte, jamais le texte lui-même
route: A
page_offset: -1                      # pdf_index0 = book_page + page_offset
boilerplate_patterns:
  - '\w+ \d{4} \w+:Layout.*?Page \d+'
drop_sections: ["Front Cover", "Credits", "Index"]
table_pages: [57, 58, 141]
notes: "Les tables de véhicules p.57 sortent mieux en mode -layout."
```

## Ce qui ne peut pas entrer dans le dépôt

- Un PDF de livre de règles, même « juste pour tester ».
- Un index, un `chunks.jsonl`, un dossier `qdrant_storage/` — ce sont des
  dérivés de l'œuvre.
- Tout fichier de plus de 1 Mio.
- Un fichier de secrets (`.env`, `.env.*`, identifiants cloud, cles privees).

Le hook pre-commit et la CI refusent ces fichiers. Activez le hook une fois :

```bash
git config core.hooksPath .githooks
```

Ce garde-fou controle les noms de fichiers, pas les secrets integres au code.
Avant publication, scannez aussi tout l'historique avec
`gitleaks git --redact --log-opts="--all" .`.

Les seules fixtures acceptées sont redistribuables (OGL, Creative Commons) et
déclarées dans [`fixtures/SOURCES.md`](fixtures/SOURCES.md).

## Contribuer du code

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

Trois règles qui viennent du cadrage du projet, pas du goût :

1. **Aucune décision d'ingestion hors du probe.** Si un module en aval doit
   savoir si le livre est un scan, il lit le `ProbeReport`.
2. **Aucune heuristique spécifique à un livre dans le code.** Un livre qui se
   détecte mal se corrige par un profil.
3. **Aucun `try/except` qui avale une erreur d'extraction.** Une page qui échoue
   produit une `PageError` visible dans le rapport.
