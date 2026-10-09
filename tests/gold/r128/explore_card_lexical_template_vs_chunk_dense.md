## furniture (graph 392a170ecc10)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 22/33 (46/73) | 26/33 (53/73), 1 short | 26/33 (54/73), 1 short | 26/33 (58/73), 1 short | 31/33 (70/73), 23 short |
| chunk_dense | 21/33 (41/73) | 26/33 (53/73) | 27/33 (59/73) | 28/33 (62/73) | 31/33 (71/73) |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 64/86 (30/50) | 74/86 (39/50), 1 short | 78/86 (42/50), 1 short | 81/86 (45/50), 1 short | 86/86 (50/50), 5 short |
| chunk_dense | 34/86 (9/50) | 46/86 (17/50) | 54/86 (21/50) | 60/86 (27/50) | 64/86 (29/50) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | card_lexical_template over chunk_dense: 2 / 1, p 1.000 | card_lexical_template over chunk_dense: 25 / 4, p 0.000 |
| 10 | card_lexical_template over chunk_dense: 3 / 3, p 1.000 | card_lexical_template over chunk_dense: 25 / 3, p 0.000 |
| 15 | chunk_dense over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over chunk_dense: 23 / 2, p 0.000 |
| 20 | chunk_dense over card_lexical_template: 3 / 1, p 0.625 | card_lexical_template over chunk_dense: 20 / 2, p 0.000 |
| 50 | chunk_dense over card_lexical_template: 2 / 2, p 1.000 | card_lexical_template over chunk_dense: 21 / 0, p 0.000 |

## heldout (graph bc7c5a1efe3d)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36) | 28/28 (36/36) | 28/28 (36/36), 1 short |
| chunk_dense | 26/28 (34/36) | 26/28 (34/36) | 26/28 (34/36) | 27/28 (35/36) | 28/28 (36/36) |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 55/65 (41/51) | 60/65 (46/51) | 60/65 (46/51) | 62/65 (48/51) | 64/65 (50/51), 2 short |
| chunk_dense | 41/65 (30/51) | 48/65 (37/51) | 57/65 (43/51) | 59/65 (45/51) | 65/65 (51/51) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over chunk_dense: 14 / 3, p 0.013 |
| 10 | chunk_dense over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over chunk_dense: 14 / 5, p 0.064 |
| 15 | card_lexical_template over chunk_dense: 2 / 1, p 1.000 | card_lexical_template over chunk_dense: 8 / 5, p 0.581 |
| 20 | card_lexical_template over chunk_dense: 1 / 0, p 1.000 | card_lexical_template over chunk_dense: 6 / 3, p 0.508 |
| 50 | card_lexical_template over chunk_dense: 0 / 0, p 1.000 | chunk_dense over card_lexical_template: 1 / 0, p 1.000 |

## generality (graph f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 32/38 (43/51), 1 short | 36/38 (49/51), 8 short | 37/38 (50/51), 14 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| chunk_dense | 34/38 (44/51) | 37/38 (49/51) | 38/38 (51/51) | 38/38 (51/51) | 38/38 (51/51), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 45/62 (26/40) | 51/62 (30/40), 2 short | 56/62 (34/40), 5 short | 58/62 (36/40), 8 short | 58/62 (36/40), 20 short |
| chunk_dense | 37/62 (16/40) | 46/62 (25/40) | 50/62 (29/40) | 55/62 (34/40) | 59/62 (37/40) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_lexical_template: 3 / 1, p 0.625 | card_lexical_template over chunk_dense: 16 / 6, p 0.052 |
| 10 | chunk_dense over card_lexical_template: 2 / 1, p 1.000 | card_lexical_template over chunk_dense: 10 / 5, p 0.302 |
| 15 | chunk_dense over card_lexical_template: 1 / 0, p 1.000 | card_lexical_template over chunk_dense: 6 / 1, p 0.125 |
| 20 | card_lexical_template over chunk_dense: 0 / 0, p 1.000 | card_lexical_template over chunk_dense: 2 / 0, p 0.500 |
| 50 | card_lexical_template over chunk_dense: 0 / 0, p 1.000 | chunk_dense over card_lexical_template: 1 / 0, p 1.000 |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 79/99 (122/160), 1 short | 87/99 (135/160), 9 short | 90/99 (139/160), 15 short | 92/99 (145/160), 21 short | 97/99 (157/160), 62 short |
| chunk_dense | 81/99 (119/160) | 89/99 (136/160) | 91/99 (144/160) | 93/99 (148/160) | 97/99 (158/160), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| card_lexical_template | 164/213 (97/141) | 185/213 (115/141), 3 short | 194/213 (122/141), 6 short | 201/213 (129/141), 9 short | 208/213 (136/141), 27 short |
| chunk_dense | 112/213 (55/141) | 140/213 (79/141) | 161/213 (93/141) | 174/213 (106/141) | 188/213 (117/141) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_lexical_template: 7 / 5, p 0.774 | card_lexical_template over chunk_dense: 55 / 13, p 0.000 |
| 10 | chunk_dense over card_lexical_template: 8 / 6, p 0.791 | card_lexical_template over chunk_dense: 49 / 13, p 0.000 |
| 15 | chunk_dense over card_lexical_template: 5 / 4, p 1.000 | card_lexical_template over chunk_dense: 37 / 8, p 0.000 |
| 20 | chunk_dense over card_lexical_template: 3 / 2, p 1.000 | card_lexical_template over chunk_dense: 28 / 5, p 0.000 |
| 50 | chunk_dense over card_lexical_template: 2 / 2, p 1.000 | card_lexical_template over chunk_dense: 21 / 2, p 0.000 |
