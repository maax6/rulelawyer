# Baseline Route A — 2026-10-05

Mesure locale sur le PDF de référence Corporation : vrais modèles BGE-M3
dense et BGE-reranker-v2-m3, Qdrant embarqué et BM25. Aucun appel OpenRouter.
Le résultat est **sous la cible** ; la démo ne suffit pas à valider le manuel.

| Mesure | Résultat |
|---|---:|
| Questions totales | 30 |
| Questions répondables évaluées pour Recall@6 | 24 |
| Recall@6 global | **60,42 %** |
| Faits | 70,00 % |
| Procédures | 62,50 % |
| Questions croisées | 41,67 % |
| Questions avec toutes les pages attendues retrouvées | 13 / 24 |
| Refus du retrieval sur les hors-livre | 2 / 3 |
| Seuil du reranker | 0,5 |
| Cible Recall@6 ≥ 85 % | **Non atteinte** |
| Réponses, citations et clarifications générées | Non mesurées |

L'ingestion produit 150 sections et 344 chunks. Taille estimée, chemin inclus :
minimum 17, médiane 992, maximum 1 200 tokens. Les sous-chunks conservent leur
provenance page par page. Les artefacts complets restent locaux dans
`.cache/evaluation/` et `reports/route-a-baseline.json`.

Les 30 cas ont été exécutés. Le cas `c06` a ensuite été précisé pour exiger deux
règles indépendantes (licence et installation), et rejoué avec les mêmes
modèles et index. La revue a aussi confirmé que la règle de `f09` figure
aux folios 139 et 144 : le folio 144, déjà récupéré, est accepté comme preuve
alternative. Les métriques ont été recalculées sur les résultats conservés,
sans modifier le seuil ni relancer les autres questions. Le résultat
ci-dessus correspond au jeu publié.

Cas répondables incomplets : `f01`, `f03`, `f08`, `p02`, `p04`, `p07`,
`c01`, `c03`, `c04`, `c05`, `c06`. Le cas hors-livre `o03` retourne une page
qui indique que le sujet dépasse ce manuel : cela ne prouve pas que la
génération hallucinerait, mais ce n'est pas un refus du retrieval.

## Diagnostic des échecs

Une recherche complémentaire avec le seuil à zéro, sur le même index et les
mêmes modèles, distingue classement et filtrage. Elle ne remplace pas la
baseline à seuil 0,5.

| Cas | Folio attendu | Rang avant filtrage | Score reranker |
|---|---:|---:|---:|
| f01 | 142 | 1 | 0,152 |
| f08 | 79 | 1 | 0,322 |
| p07 | 145 | 1 | 0,165 |
| c03 | 46 | 1 | 0,498 |
| c03 | 31 | 9 | 0,031 |

Plusieurs bonnes pages sont donc filtrées ; `c03` exige également d'améliorer
le classement de sa deuxième preuve. La prochaine étape est de calibrer le
seuil sur un jeu distinct et d'améliorer la couverture des questions croisées,
puis de rejouer cette baseline. Abaisser le seuil seulement pour atteindre
85 % sur ces 30 questions ne validerait pas la qualité du refus hors livre.

## Reproduction

```bash
uv run --extra index python eval/run_eval.py \
  --pdf /chemin/Corporation-RPG-Core-Rulebook.pdf \
  --output reports/route-a-baseline.json
```

L'exécution doit retourner 1 tant que la cible n'est pas atteinte. Les
métriques de génération restent nulles sans `--generate` ; aucune clé n'était
configurée dans le processus de cette mesure.

Empreintes des entrées :

- PDF : `7bb816428e9b8bdf3f6c0820b9cd710318c9dbf753f9bb3d4ec1ac9ab806e507`.
- Questions : `1ea1f5ff6f8d6fd8c6e355cda7ee6969c591475cd9f0c1283d191da1a7f2a51f`.
- Profil : `bfd9a45810be73868f98bba42e4f1cc04d71ac7b47fc0071df7ea7e7be956811`.

Environnement : FlagEmbedding 1.3.5, torch 2.13.0, transformers 4.47.1,
qdrant-client 1.19.0, bm25s 0.3.10. Révisions des modèles en cache :
BGE-M3 `5617a9f61b028005a4858fdac845db406aefb181` ; reranker
`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.

## Vérifications de l'implémentation

98 tests passent en local, y compris les 12 tests sur le PDF commercial.
Lint, formatage et types passent ; la construction du wheel et du sdist
réussit. Les nouveaux modules figurent dans le wheel ; le sdist contient les
outils d'évaluation et exclut le PDF commercial et les caches.

Revue Standards : choix d'ordre d'extraction déplacé dans le rapport du probe
et surcharge de profil disponible ; aucune remarque restante.
Revue Spec : chevauchement des paragraphes conservé quand une unité entière
tient dans le budget, avec test de régression ; aucune remarque restante.
