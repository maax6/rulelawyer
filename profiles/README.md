# Profils

Un profil surcharge l'auto-détection du probe pour un livre précis. Il ne
contient que des paramètres et des empreintes — jamais de contenu.

Voir [`../CONTRIBUTING.md`](../CONTRIBUTING.md) pour le format et la marche à
suivre. `rulelawyer profile init <pdf>` génère un squelette pré-rempli.

```bash
rulelawyer profile init mon-livre.pdf --name "Mon JDR" > profiles/mon-jdr.yaml
rulelawyer probe mon-livre.pdf --profile profiles/mon-jdr.yaml
rulelawyer ingest mon-livre.pdf --profiles-dir profiles/ --max-tokens 1200
```

Le squelette contient les empreintes, la route détectée et des listes vides ;
il n'invente pas un offset global depuis une pagination partielle. Vérifiez les
folios avant d'ajouter `page_offset`.

| Paramètre | Effet |
|---|---|
| `name` | Nom affiché dans le diagnostic. Le nom du fichier sans extension devient `book_id`. |
| `match` | Tous les critères doivent correspondre : `page_count`, `producer_contains`, `first_page_text_sha256`, `file_sha256`. Exige une empreinte, ou pages + producteur. |
| `route` | Surcharge la recommandation dans le probe. L'ingestion refuse toujours les routes non implémentées. |
| `reading_order` | `text_flow` (défaut Route A) ou `position`. Le diagnostic géométrique reste utilisé pour mesurer les colonnes et la table des matières ; l'ordre d'extraction choisi est enregistré dans le rapport et consommé par l'ingestion. |
| `page_offset` | Convention : `pdf_index0 = book_page + page_offset`. Surcharge uniforme explicite ; les pages imprimées non positives ne sont pas citées. |
| `boilerplate_patterns` | Expressions régulières supplémentaires : les lignes correspondantes sont retirées de l'ingestion. |
| `drop_sections` | Titres d'outline à exclure, avec leurs descendants. Leurs bornes restent utilisées pour ne pas absorber leur texte dans la section précédente. |
| `table_pages` | Folios imprimés, 1-based : les chunks concernés sont marqués `table`. Ce marquage n'effectue pas de parsing de tableaux. |
| `heading_overrides` | Titre d'outline → `{text: titre exact dans le flux PDF, occurrence: 1}`. Permet de résoudre un intitulé différent ou répété ; occurrence 1-based sur la page de destination. |
| `notes` | Explications de l'auteur, sans effet automatique. |

Le matching automatique lit les fichiers `.yaml` et `.yml` du dossier choisi.
Les champs inconnus, motifs invalides et matches ambigus échouent explicitement.
Un profil choisi avec `--profile` doit lui aussi correspondre au PDF.
`--route A/B/C/D` reste une surcharge explicite enregistrée par le probe.

`corporation-core.yaml` décrit l'édition du PDF commercial de développement.
Le livre, ses passages, son index et ses rapports ne sont jamais distribués.
