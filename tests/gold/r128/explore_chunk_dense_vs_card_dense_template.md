## furniture (graph 392a170ecc10)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 21/33 (41/73) | 26/33 (53/73) | 27/33 (59/73) | 28/33 (62/73) | 31/33 (71/73) |
| card_dense_template | 20/33 (45/73) | 25/33 (53/73) | 25/33 (54/73) | 25/33 (57/73) | 32/33 (70/73), 22 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 34/86 (9/50) | 46/86 (17/50) | 54/86 (21/50) | 60/86 (27/50) | 64/86 (29/50) |
| card_dense_template | 68/86 (34/50) | 76/86 (42/50) | 82/86 (46/50) | 84/86 (48/50) | 85/86 (49/50) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_dense_template: 2 / 1, p 1.000 | card_dense_template over chunk_dense: 27 / 2, p 0.000 |
| 10 | chunk_dense over card_dense_template: 3 / 2, p 1.000 | card_dense_template over chunk_dense: 26 / 1, p 0.000 |
| 15 | chunk_dense over card_dense_template: 3 / 1, p 0.625 | card_dense_template over chunk_dense: 28 / 3, p 0.000 |
| 20 | chunk_dense over card_dense_template: 3 / 0, p 0.250 | card_dense_template over chunk_dense: 23 / 2, p 0.000 |
| 50 | card_dense_template over chunk_dense: 2 / 1, p 1.000 | card_dense_template over chunk_dense: 21 / 1, p 0.000 |

## heldout (graph bc7c5a1efe3d)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 26/28 (34/36) | 26/28 (34/36) | 26/28 (34/36) | 27/28 (35/36) | 28/28 (36/36) |
| card_dense_template | 22/28 (30/36) | 24/28 (32/36) | 25/28 (33/36) | 25/28 (33/36) | 28/28 (36/36), 6 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 41/65 (30/51) | 48/65 (37/51) | 57/65 (43/51) | 59/65 (45/51) | 65/65 (51/51) |
| card_dense_template | 53/65 (40/51) | 55/65 (41/51) | 57/65 (43/51) | 58/65 (44/51) | 62/65 (48/51) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_dense_template: 6 / 2, p 0.289 | card_dense_template over chunk_dense: 11 / 1, p 0.006 |
| 10 | chunk_dense over card_dense_template: 4 / 2, p 0.688 | card_dense_template over chunk_dense: 8 / 4, p 0.388 |
| 15 | chunk_dense over card_dense_template: 3 / 2, p 1.000 | chunk_dense over card_dense_template: 6 / 6, p 1.000 |
| 20 | chunk_dense over card_dense_template: 3 / 1, p 0.625 | chunk_dense over card_dense_template: 5 / 4, p 1.000 |
| 50 | chunk_dense over card_dense_template: 0 / 0, p 1.000 | chunk_dense over card_dense_template: 3 / 0, p 0.250 |

## generality (graph f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 34/38 (44/51) | 37/38 (49/51) | 38/38 (51/51) | 38/38 (51/51) | 38/38 (51/51), 38 short |
| card_dense_template | 31/38 (40/51) | 34/38 (47/51) | 37/38 (50/51), 7 short | 37/38 (50/51), 20 short | 38/38 (51/51), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 37/62 (16/40) | 46/62 (25/40) | 50/62 (29/40) | 55/62 (34/40) | 59/62 (37/40) |
| card_dense_template | 40/62 (20/40) | 47/62 (25/40) | 52/62 (30/40) | 53/62 (31/40) | 54/62 (32/40) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_dense_template: 4 / 1, p 0.375 | card_dense_template over chunk_dense: 13 / 9, p 0.523 |
| 10 | chunk_dense over card_dense_template: 3 / 0, p 0.250 | card_dense_template over chunk_dense: 6 / 6, p 1.000 |
| 15 | chunk_dense over card_dense_template: 1 / 0, p 1.000 | card_dense_template over chunk_dense: 2 / 1, p 1.000 |
| 20 | chunk_dense over card_dense_template: 1 / 0, p 1.000 | chunk_dense over card_dense_template: 4 / 1, p 0.375 |
| 50 | chunk_dense over card_dense_template: 0 / 0, p 1.000 | chunk_dense over card_dense_template: 5 / 0, p 0.062 |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 81/99 (119/160) | 89/99 (136/160) | 91/99 (144/160) | 93/99 (148/160) | 97/99 (158/160), 38 short |
| card_dense_template | 73/99 (115/160) | 83/99 (132/160) | 87/99 (137/160), 7 short | 87/99 (140/160), 20 short | 98/99 (157/160), 66 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 112/213 (55/141) | 140/213 (79/141) | 161/213 (93/141) | 174/213 (106/141) | 188/213 (117/141) |
| card_dense_template | 161/213 (94/141) | 178/213 (108/141) | 191/213 (119/141) | 195/213 (123/141) | 201/213 (129/141) |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over card_dense_template: 12 / 4, p 0.077 | card_dense_template over chunk_dense: 51 / 12, p 0.000 |
| 10 | chunk_dense over card_dense_template: 10 / 4, p 0.180 | card_dense_template over chunk_dense: 40 / 11, p 0.000 |
| 15 | chunk_dense over card_dense_template: 7 / 3, p 0.344 | card_dense_template over chunk_dense: 36 / 10, p 0.000 |
| 20 | chunk_dense over card_dense_template: 7 / 1, p 0.070 | card_dense_template over chunk_dense: 28 / 11, p 0.009 |
| 50 | card_dense_template over chunk_dense: 2 / 1, p 1.000 | card_dense_template over chunk_dense: 21 / 9, p 0.043 |
