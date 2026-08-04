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
| _(aucune pour l'instant)_ | | | | |

## Fixtures recherchées

Le probe a besoin d'au moins un cas par route pour ne pas se calibrer sur le cas
facile :

- [ ] **Route A** — natif, outline propre
- [ ] **Route B** — natif, aucun outline (la hiérarchie doit venir de la table
      des matières textuelle)
- [ ] **Route C** — scan sans couche texte, et si possible un exemplaire avec
      une *mauvaise* couche OCR : c'est le cas piège
- [ ] multi-colonnes variable dans un même livre
- [ ] fond texturé pleine page
