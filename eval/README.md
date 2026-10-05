# Évaluation du manuel local

`questions.yaml` contient 30 questions originales, des folios et des critères
courts de vérification : 10 factuelles, 8 procédurales, 6 croisées, 3 hors-livre
et 3 ambiguës. Il ne contient aucun passage du manuel. Le hash identifie
l'édition exacte ; le runner refuse un autre PDF.

```bash
uv run --extra index python eval/run_eval.py --pdf /chemin/manuel.pdf
# Génération optionnelle : passages envoyés à OpenRouter, clé requise.
uv run --extra index python eval/run_eval.py --pdf /chemin/manuel.pdf --generate
```

Options utiles : `--questions`, `--profile`, `--profiles-dir`, `--threshold`,
`--max-tokens`, `--cache-dir` et `--output`. Le seuil d’admission par défaut est 0.1. Il s’applique au meilleur score
de la requête ; les preuves complémentaires ne sont pas filtrées une à une.
Le résultat final retient au maximum six pages PDF distinctes, avec le meilleur
fragment de chaque page. Un folio retrouvé ne prouve pas à lui seul la
complétude mécanique du fragment fourni.
La première exécution peut télécharger les modèles ; aucune API de génération
n'est appelée en mode retrieval. Les artefacts sont dans `.cache/evaluation/`
et `reports/evaluation.json`, ignorés par Git.

Le rapport ne sauvegarde ni passages ni réponses générées : uniquement
identifiants, folios attendus/retrouvés, métriques et erreurs anonymisées.

Une règle présente à plusieurs endroits peut déclarer `page_alternatives`
pour un folio attendu : retrouver ou citer l'une de ces pages suffit pour cette
règle. Une question croisée exige toujours chaque groupe de preuves attendu.
Les groupes `must_contain_any` acceptent plusieurs formulations explicites
d'un même critère. Ces alternatives sont vérifiées dans le manuel.

| Mesure | Définition |
|---|---|
| Recall@6 | Moyenne par question de la fraction des folios attendus présents dans les six passages récupérés. Un passage portant le même folio ne compte pas deux fois. Les 24 questions répondables sont incluses ; ambiguïtés et hors-livre sont exclus. |
| Recall par catégorie | Même mesure, séparée entre faits, procédures et questions croisées. |
| Refus du retrieval | Proportion des trois questions hors-livre avec zéro résultat après seuil. Ce n'est pas une mesure de refus du modèle génératif. |
| Réponses conformes | Termes exigés présents, termes interdits absents, citations exactes et toutes les pages attendues citées. Les correspondances respectent les frontières de mots et nombres. |
| Présence / exactitude des citations | Sur les 24 questions répondables, présence d'au moins un `(p. N)` ; exactitude si chaque page citée est attendue et appartient au passage fourni. Une citation valide peut rester incomplète sur une question croisée. |
| Refus correct | Réponse explicite de refus sans citation pour une question hors-livre. |
| Clarification | Une question de clarification contenant `?` et satisfaisant les critères, sans citation, pour une entrée ambiguë. Ce contrôle textuel reste à compléter par une revue humaine. |

Les métriques de génération sont `null` lorsque `--generate` n'est pas demandé.
Une erreur de génération compte comme un échec, jamais comme un cas ignoré.
La sortie vaut 0 si Recall@6 ≥ 0.85 ; en mode génération, il faut également
que chaque cas passe ses contrôles de réponse, refus ou clarification.
La sortie vaut 1 pour une évaluation sous la cible et 2 pour une exécution
impossible (mauvais PDF, configuration, dépendances ou extraction).

L'agent actuel reçoit seulement le premier passage : les réponses croisées et
les demandes de clarification constituent des limites à mesurer, puis à
implémenter. Les critères de mots ne prouvent pas seuls la justesse sémantique
d'une réponse ; relire les cas reste nécessaire.

Le chevauchement du découpage vise 15 % en conservant des unités sémantiques
entières. Il peut être plus grand si un paragraphe dépasse cette cible, ou nul
si aucune unité entière ne tient avec le nouveau texte ; la borne de taille
reste prioritaire. Les sections découpées aux sous-titres n'ont pas besoin de
ce chevauchement de dernier recours.

Les tests automatisés utilisent un index à résultats connus pour vérifier les
métriques. Une mesure de qualité du retrieval exige les vrais modèles BGE sur
le manuel, avec le rapport local correspondant.

La [baseline du manuel de référence](baseline.md) documente la mesure réelle,
ses entrées et la cible alors non atteinte. La
[correction du retrieval](retrieval-improvement.md) atteint 89,58 % sans
modifier le jeu, avec calibration distincte et limites documentées.

## Calibration distincte

```bash
HF_HOME=/tmp/rulelawyer-hf-cache uv run --extra index python eval/calibrate_retrieval.py
```

Le corpus CC0 `fixtures/retrieval-calibration.yaml` ne contient ni texte ni
question du manuel. Le seuil est choisi parmi 1, 0,1, …, 0,000001 en maximisant l’exactitude
équilibrée : moyenne du taux de preuves positives au-dessus du seuil et du
taux de requêtes négatives en dessous. À égalité, le seuil le plus haut gagne.
Les cas croisés n’exigent qu’une première preuve pour admettre la requête.
Le reste du contexte demeure candidat, afin de ne pas rejeter les autres
parties de la question sur leur score absolu. La calibration mesure
uniquement l’admission, pas Recall@6 sur un livre réel.

Avec BGE-reranker-v2-m3 : minimum positif 0,003246, seuil retenu 0,1,
15/19 preuves positives au-dessus du seuil et 6/6 négatifs refusés, soit
89,47 % d’exactitude équilibrée. Le candidat 0,001 admettrait 19/19 positifs
mais ne refuserait que 4/6 négatifs (83,33 % équilibré). Le corpus minuscule n’est pas une validation du
refus sur les ouvrages réels. Le rapport et les caches restent locaux.
