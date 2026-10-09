## furniture (graph 392a170ecc10)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 24/33 (49/73) | 26/33 (59/73) | 27/33 (65/73) | 28/33 (66/73), 3 short | 30/33 (69/73), 30 short |
| card_lexical_template | 22/33 (46/73) | 26/33 (53/73), 1 short | 26/33 (54/73), 1 short | 26/33 (58/73), 1 short | 31/33 (70/73), 23 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 33/86 (10/50) | 48/86 (18/50) | 59/86 (25/50) | 66/86 (31/50) | 75/86 (39/50) |
| card_lexical_template | 64/86 (30/50) | 74/86 (39/50), 1 short | 78/86 (42/50), 1 short | 81/86 (45/50), 1 short | 86/86 (50/50), 5 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_lexical over card_lexical_template: 3 / 1, p 0.625 | card_lexical_template over chunk_lexical: 25 / 5, p 0.000 |
| 10 | chunk_lexical over card_lexical_template: 2 / 2, p 1.000 | card_lexical_template over chunk_lexical: 24 / 3, p 0.000 |
| 15 | chunk_lexical over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over chunk_lexical: 19 / 2, p 0.000 |
| 20 | chunk_lexical over card_lexical_template: 4 / 2, p 0.688 | card_lexical_template over chunk_lexical: 15 / 1, p 0.001 |
| 50 | card_lexical_template over chunk_lexical: 1 / 0, p 1.000 | card_lexical_template over chunk_lexical: 11 / 0, p 0.001 |

## heldout (graph bc7c5a1efe3d)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 25/28 (32/36) | 25/28 (33/36) | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36), 3 short |
| card_lexical_template | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36) | 28/28 (36/36) | 28/28 (36/36), 1 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 38/65 (26/51) | 46/65 (34/51) | 55/65 (41/51) | 57/65 (43/51) | 65/65 (51/51) |
| card_lexical_template | 55/65 (41/51) | 60/65 (46/51) | 60/65 (46/51) | 62/65 (48/51) | 64/65 (50/51), 2 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | card_lexical_template over chunk_lexical: 2 / 2, p 1.000 | card_lexical_template over chunk_lexical: 18 / 3, p 0.001 |
| 10 | chunk_lexical over card_lexical_template: 2 / 2, p 1.000 | card_lexical_template over chunk_lexical: 17 / 5, p 0.017 |
| 15 | card_lexical_template over chunk_lexical: 2 / 0, p 0.500 | card_lexical_template over chunk_lexical: 10 / 5, p 0.302 |
| 20 | card_lexical_template over chunk_lexical: 3 / 0, p 0.250 | card_lexical_template over chunk_lexical: 8 / 3, p 0.227 |
| 50 | card_lexical_template over chunk_lexical: 1 / 0, p 1.000 | chunk_lexical over card_lexical_template: 1 / 0, p 1.000 |

## generality (graph f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 37/38 (47/51) | 37/38 (48/51), 2 short | 38/38 (51/51), 11 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| card_lexical_template | 32/38 (43/51), 1 short | 36/38 (49/51), 8 short | 37/38 (50/51), 14 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 31/62 (17/40) | 47/62 (26/40) | 48/62 (27/40) | 55/62 (34/40) | 60/62 (38/40) |
| card_lexical_template | 45/62 (26/40) | 51/62 (30/40), 2 short | 56/62 (34/40), 5 short | 58/62 (36/40), 8 short | 58/62 (36/40), 20 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_lexical over card_lexical_template: 5 / 0, p 0.062 | card_lexical_template over chunk_lexical: 15 / 6, p 0.078 |
| 10 | chunk_lexical over card_lexical_template: 2 / 1, p 1.000 | card_lexical_template over chunk_lexical: 9 / 5, p 0.424 |
| 15 | chunk_lexical over card_lexical_template: 1 / 0, p 1.000 | card_lexical_template over chunk_lexical: 9 / 2, p 0.065 |
| 20 | chunk_lexical over card_lexical_template: 0 / 0, p 1.000 | card_lexical_template over chunk_lexical: 2 / 0, p 0.500 |
| 50 | chunk_lexical over card_lexical_template: 0 / 0, p 1.000 | chunk_lexical over card_lexical_template: 2 / 0, p 0.500 |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 86/99 (128/160) | 88/99 (140/160), 2 short | 90/99 (149/160), 11 short | 91/99 (150/160), 23 short | 95/99 (155/160), 71 short |
| card_lexical_template | 79/99 (122/160), 1 short | 87/99 (135/160), 9 short | 90/99 (139/160), 15 short | 92/99 (145/160), 21 short | 97/99 (157/160), 62 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_lexical | 102/213 (53/141) | 141/213 (78/141) | 162/213 (93/141) | 178/213 (108/141) | 200/213 (128/141) |
| card_lexical_template | 164/213 (97/141) | 185/213 (115/141), 3 short | 194/213 (122/141), 6 short | 201/213 (129/141), 9 short | 208/213 (136/141), 27 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_lexical over card_lexical_template: 10 / 3, p 0.092 | card_lexical_template over chunk_lexical: 58 / 14, p 0.000 |
| 10 | chunk_lexical over card_lexical_template: 6 / 5, p 1.000 | card_lexical_template over chunk_lexical: 50 / 13, p 0.000 |
| 15 | chunk_lexical over card_lexical_template: 4 / 4, p 1.000 | card_lexical_template over chunk_lexical: 38 / 9, p 0.000 |
| 20 | card_lexical_template over chunk_lexical: 5 / 4, p 1.000 | card_lexical_template over chunk_lexical: 25 / 4, p 0.000 |
| 50 | card_lexical_template over chunk_lexical: 2 / 0, p 0.500 | card_lexical_template over chunk_lexical: 11 / 3, p 0.057 |
