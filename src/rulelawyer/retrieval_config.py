"""Seuil de recevabilité calibré hors du manuel de référence.

Puissance de dix maximisant l'exactitude équilibrée sur le corpus original
fixtures/retrieval-calibration.yaml, sans les questions du manuel. Ce score
n'est pas une probabilité de justesse ; l'agent doit vérifier ses preuves.
"""

DEFAULT_THRESHOLD = 0.1
