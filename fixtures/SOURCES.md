# Fixtures

Ce dossier ne peut accueillir que des PDF **explicitement redistribuables** :
quickstarts gratuits, jeux sous OGL ou Creative Commons. Chaque fichier doit
être listé ici avec sa provenance et sa licence, sans quoi le hook pre-commit
et la CI le refusent.

Le PDF de développement principal (257 pages, Route A) n'est **pas** ici et n'y
sera jamais : il est commercial. Les tests le trouvent via la variable
d'environnement `RULELAWYER_FIXTURE_PDF` et se sautent s'il est absent.

```bash
RULELAWYER_FIXTURE_PDF=/chemin/vers/mon-livre.pdf uv run pytest
```

## Fixtures déclarées

| Fichier | Jeu | Licence | Source | Sert à tester |
|---|---|---|---|---|
| `fixtures/knave.pdf` | Knave 1.0, Ben Milton (Questing Beast) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — la page de copyright du PDF et la page de l'éditeur disent « You are free to share and adapt this material » | https://questingbeast.itch.io/knave | Route B : PDF natif (Word), aucun outline, pagination non mesurée |

Une fixture synthétique est **générée en temporaire**, jamais ajoutée ici :
[`scripts/make_demo_pdf.py`](../scripts/make_demo_pdf.py), « Manuel des Veilleurs ».
Son texte original (deux règles inventées) est sous CC0. Le PDF natif comporte
quatre pages, un outline hiérarchique et les folios 41–44. Les tests le créent
dans `tmp_path` ; la commande de démo du README l'écrit dans `/tmp`.

[`retrieval-calibration.yaml`](retrieval-calibration.yaml), « Lantern Keepers »,
contient six règles originales et 25 questions, sous CC0. Aucun texte ni
question du manuel commercial. Les 19 cas répondables (dont trois croisés)
et six hors-corpus servent uniquement à calibrer l'admission du retrieval ;
ce petit jeu synthétique ne valide pas la qualité sur un livre réel.

## Fixtures recherchées

Le probe a besoin d'au moins un cas par route pour ne pas se calibrer sur le cas
facile :

- [ ] **Route A** — natif, outline propre
- [x] **Route B** — natif, aucun outline (la hiérarchie doit venir de la table
      des matières textuelle) — `fixtures/knave.pdf`
- [ ] **Route C** — scan sans couche texte, et si possible un exemplaire avec
      une *mauvaise* couche OCR : c'est le cas piège
- [ ] multi-colonnes variable dans un même livre
- [ ] fond texturé pleine page
