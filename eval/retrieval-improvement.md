# Retrieval Route A — 2026-10-05

**Recall@6 : 60,42 % → 89,58 %**, cible de 85 % atteinte avant de modifier
l'agent. Les 30 cas ont été rejoués avec BGE-M3, BGE-reranker-v2-m3,
Qdrant local et BM25. Aucun appel LLM, aucun changement du prompt, du PDF,
du profil, des chunks ou des questions du manuel.

| Mesure | Baseline | Après correction |
|---|---:|---:|
| Recall@6 (24 répondables) | 60,42 % | **89,58 %** |
| Faits | 70,00 % | 100,00 % |
| Procédures | 62,50 % | 75,00 % |
| Questions croisées | 41,67 % | 91,67 % |
| Toutes les pages attendues retrouvées | 13 / 24 | 21 / 24 |
| Refus du retrieval hors-livre | 2 / 3 | 2 / 3 |
| Génération / citations / clarification | Non mesurées | Non mesurées |

## Décisions et arbitrages

Le seuil historique 0,5 s'appliquait à chaque fragment. Une preuve qui ne
répond qu'à une partie d'une question composée recevait souvent un score
faible, puis disparaissait. Le seuil décide désormais de l'admission de la
**requête**, sur son meilleur score. Si elle est admise, ses six meilleurs
passages demeurent candidats, y compris les preuves complémentaires faibles.

Le score normalisé BGE n'est pas une probabilité calibrée : la documentation
[explique la sigmoïde](https://bge-model.com/tutorial/5_Reranking/5.2.html).
Le seuil est choisi **hors du manuel**, sur le corpus original CC0 Lantern
Keepers (six règles, 19 requêtes répondables, six hors-corpus, français/anglais).
Parmi les puissances de dix de 1 à 0,000001, nous maximisons l'exactitude
équilibrée ; à égalité, le seuil le plus haut gagne. Cela donne **0,1** :
15/19 preuves positives au-dessus du seuil et 6/6 négatifs refusés,
89,47 % équilibré. Le candidat 0,001 conserverait les 19 positifs mais
laisserait passer deux négatifs (83,33 % équilibré) ; il n'est pas retenu.
Ce corpus minuscule reste une calibration, pas une validation indépendante
sur plusieurs livres réels.

Nous réservons également les six places à des pages PDF distinctes, en
gardant le fragment le mieux classé de chacune. Les doublons introduits par
le chevauchement ne consomment plus deux places. Cette protection est
vérifiée par un test synthétique ; elle n'ajoute aucun gain de rappel sur
ce manuel après les deux corrections précédentes.

Décomposition des résultats sur les **mêmes scores locaux**, sans autre
réglage (ces mesures expliquent la correction, elles ne choisissent pas le seuil) :

| Politique | Recall@6 |
|---|---:|
| Filtre par fragment, seuil 0,5 | 60,42 % |
| Admission par requête, seuil 0,5 | 70,83 % |
| Admission par requête, seuil calibré 0,1 | 89,58 % |
| Puis pages distinctes | 89,58 % |

Les limites sont explicites : `p02` et `p04` sont encore refusées ; `c03`
ne retrouve que la règle de recharge, pas celle de réparation. Le cas `o03`
reste accepté par le retrieval, sans que cela prouve la justesse d'une réponse.
Nous ne modifions ni leurs questions ni leur traitement pour obtenir un
meilleur chiffre. La présence d'un folio ne prouve pas que le fragment
sélectionné contient toutes les règles demandées. Les preuves secondaires
peuvent être faibles : l'agent doit pouvoir refuser malgré du contexte.

## Reproduction et provenance

```bash
HF_HOME=/tmp/rulelawyer-hf-cache uv run --extra index python eval/calibrate_retrieval.py
HF_HOME=/tmp/rulelawyer-hf-cache uv run --extra index python eval/run_eval.py \
  --pdf "$PWD/Corporation-RPG-Core-Rulebook.pdf" \
  --output reports/route-a-retrieval-improved.json
```

Le runner retourne 0 en mode retrieval. Tous les scores des 30 cas ont été
recalculés avec les modèles réels. Le dernier passage dans le runner réutilise
ces appels numériques en cache local et exécute la nouvelle recherche ainsi
que le calcul des métriques ; aucun résultat attendu n'alimente le retrieval.
La décomposition ci-dessus utilise les mêmes scores. Les rapports et les
artefacts du manuel restent ignorés par Git.

Les empreintes du PDF, du profil et des questions sont inchangées par rapport
à la [baseline](baseline.md), ainsi que les versions et révisions des modèles.
Corpus de calibration :
`2c7d708a3e672eb030a76186821b24fdd3f072bdf648afc1417945660b9c782f`.
Lint, formatage, mypy et **103 tests**, dont les 12 tests du PDF commercial,
passent avant le commit de cette étape.
