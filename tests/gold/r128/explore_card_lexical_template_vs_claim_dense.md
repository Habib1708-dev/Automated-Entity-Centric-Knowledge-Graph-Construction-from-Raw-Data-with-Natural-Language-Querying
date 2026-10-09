## furniture (graph 392a170ecc10)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 22/33 (46/73) | 26/33 (53/73), 1 short | 26/33 (54/73), 1 short | 26/33 (58/73), 1 short | 31/33 (70/73), 23 short |
| claim_dense | 26/33 (49/73) | 29/33 (60/73), 4 short | 30/33 (61/73), 10 short | 30/33 (63/73), 15 short | 30/33 (65/73), 33 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 64/86 (30/50) | 74/86 (39/50), 1 short | 78/86 (42/50), 1 short | 81/86 (45/50), 1 short | 86/86 (50/50), 5 short |
| claim_dense | 65/86 (31/50) | 68/86 (33/50) | 70/86 (35/50) | 70/86 (35/50) | 74/86 (39/50), 25 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | claim_dense over card_lexical_template: 4 / 0, p 0.125 | claim_dense over card_lexical_template: 11 / 10, p 1.000 |
| 10 | claim_dense over card_lexical_template: 4 / 1, p 0.375 | card_lexical_template over claim_dense: 12 / 6, p 0.238 |
| 15 | claim_dense over card_lexical_template: 5 / 1, p 0.219 | card_lexical_template over claim_dense: 12 / 5, p 0.143 |
| 20 | claim_dense over card_lexical_template: 5 / 1, p 0.219 | card_lexical_template over claim_dense: 13 / 3, p 0.021 |
| 50 | card_lexical_template over claim_dense: 2 / 1, p 1.000 | card_lexical_template over claim_dense: 11 / 0, p 0.001 |

## heldout (graph bc7c5a1efe3d)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36) | 28/28 (36/36) | 28/28 (36/36), 1 short |
| claim_dense | 23/28 (31/36) | 24/28 (32/36) | 24/28 (32/36), 1 short | 25/28 (33/36), 6 short | 27/28 (35/36), 28 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 55/65 (41/51) | 60/65 (46/51) | 60/65 (46/51) | 62/65 (48/51) | 64/65 (50/51), 2 short |
| claim_dense | 45/65 (32/51) | 51/65 (38/51) | 53/65 (39/51) | 55/65 (41/51) | 57/65 (43/51) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | card_lexical_template over claim_dense: 3 / 1, p 0.625 | card_lexical_template over claim_dense: 13 / 4, p 0.049 |
| 10 | card_lexical_template over claim_dense: 2 / 1, p 1.000 | card_lexical_template over claim_dense: 12 / 4, p 0.077 |
| 15 | card_lexical_template over claim_dense: 3 / 0, p 0.250 | card_lexical_template over claim_dense: 11 / 4, p 0.118 |
| 20 | card_lexical_template over claim_dense: 3 / 0, p 0.250 | card_lexical_template over claim_dense: 10 / 3, p 0.092 |
| 50 | card_lexical_template over claim_dense: 1 / 0, p 1.000 | card_lexical_template over claim_dense: 8 / 1, p 0.039 |

## generality (graph f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 32/38 (43/51), 1 short | 36/38 (49/51), 8 short | 37/38 (50/51), 14 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| claim_dense | 33/38 (44/51) | 37/38 (50/51), 5 short | 38/38 (51/51), 17 short | 38/38 (51/51), 35 short | 38/38 (51/51), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 45/62 (26/40) | 51/62 (30/40), 2 short | 56/62 (34/40), 5 short | 58/62 (36/40), 8 short | 58/62 (36/40), 20 short |
| claim_dense | 41/62 (19/40) | 43/62 (21/40) | 44/62 (22/40) | 46/62 (24/40) | 47/62 (25/40), 15 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | claim_dense over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over claim_dense: 10 / 3, p 0.092 |
| 10 | claim_dense over card_lexical_template: 2 / 1, p 1.000 | card_lexical_template over claim_dense: 12 / 3, p 0.035 |
| 15 | claim_dense over card_lexical_template: 1 / 0, p 1.000 | card_lexical_template over claim_dense: 13 / 1, p 0.002 |
| 20 | card_lexical_template over claim_dense: 0 / 0, p 1.000 | card_lexical_template over claim_dense: 12 / 0, p 0.000 |
| 50 | card_lexical_template over claim_dense: 0 / 0, p 1.000 | card_lexical_template over claim_dense: 11 / 0, p 0.001 |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 79/99 (122/160), 1 short | 87/99 (135/160), 9 short | 90/99 (139/160), 15 short | 92/99 (145/160), 21 short | 97/99 (157/160), 62 short |
| claim_dense | 82/99 (124/160) | 90/99 (142/160), 9 short | 92/99 (144/160), 28 short | 93/99 (147/160), 56 short | 95/99 (151/160), 99 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 164/213 (97/141) | 185/213 (115/141), 3 short | 194/213 (122/141), 6 short | 201/213 (129/141), 9 short | 208/213 (136/141), 27 short |
| claim_dense | 151/213 (82/141) | 162/213 (92/141) | 167/213 (96/141) | 171/213 (100/141) | 178/213 (107/141), 40 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | claim_dense over card_lexical_template: 8 / 5, p 0.581 | card_lexical_template over claim_dense: 33 / 18, p 0.049 |
| 10 | claim_dense over card_lexical_template: 7 / 4, p 0.549 | card_lexical_template over claim_dense: 36 / 13, p 0.001 |
| 15 | claim_dense over card_lexical_template: 6 / 4, p 0.754 | card_lexical_template over claim_dense: 36 / 10, p 0.000 |
| 20 | claim_dense over card_lexical_template: 5 / 4, p 1.000 | card_lexical_template over claim_dense: 35 / 6, p 0.000 |
| 50 | card_lexical_template over claim_dense: 3 / 1, p 0.625 | card_lexical_template over claim_dense: 30 / 1, p 0.000 |
