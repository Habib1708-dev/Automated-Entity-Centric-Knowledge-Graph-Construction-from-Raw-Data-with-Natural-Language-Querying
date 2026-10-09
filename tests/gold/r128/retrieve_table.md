## furniture (graph 392a170ecc10)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 21/33 (41/73) | 26/33 (53/73) | 27/33 (59/73) | 28/33 (62/73) | 31/33 (71/73) |
| chunk_lexical | 24/33 (49/73) | 26/33 (59/73) | 27/33 (65/73) | 28/33 (66/73), 3 short | 30/33 (69/73), 30 short |
| claim_dense | 26/33 (49/73) | 29/33 (60/73), 4 short | 30/33 (61/73), 10 short | 30/33 (63/73), 15 short | 30/33 (65/73), 33 short |
| claim_lexical | 24/33 (46/73), 1 short | 27/33 (52/73), 6 short | 28/33 (58/73), 12 short | 29/33 (61/73), 16 short | 29/33 (62/73), 33 short |
| card_dense_template | 20/33 (45/73) | 25/33 (53/73) | 25/33 (54/73) | 25/33 (57/73) | 32/33 (70/73), 22 short |
| card_lexical_template | 22/33 (46/73) | 26/33 (53/73), 1 short | 26/33 (54/73), 1 short | 26/33 (58/73), 1 short | 31/33 (70/73), 23 short |
| card_dense_summary | 21/33 (42/73) | 25/33 (52/73) | 27/33 (60/73) | 28/33 (62/73) | 32/33 (68/73), 22 short |
| card_lexical_summary | 21/33 (44/73) | 26/33 (54/73), 1 short | 26/33 (54/73), 1 short | 27/33 (55/73), 1 short | 31/33 (69/73), 16 short |
| graph_retrieval | 24/33 (47/73), 2 short | 28/33 (58/73), 13 short | 29/33 (59/73), 22 short | 29/33 (59/73), 26 short | 29/33 (59/73), 33 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 34/86 (9/50) | 46/86 (17/50) | 54/86 (21/50) | 60/86 (27/50) | 64/86 (29/50) |
| chunk_lexical | 33/86 (10/50) | 48/86 (18/50) | 59/86 (25/50) | 66/86 (31/50) | 75/86 (39/50) |
| claim_dense | 65/86 (31/50) | 68/86 (33/50) | 70/86 (35/50) | 70/86 (35/50) | 74/86 (39/50), 25 short |
| claim_lexical | 70/86 (35/50) | 71/86 (36/50), 1 short | 72/86 (37/50), 1 short | 73/86 (38/50), 1 short | 74/86 (39/50), 24 short |
| card_dense_template | 68/86 (34/50) | 76/86 (42/50) | 82/86 (46/50) | 84/86 (48/50) | 85/86 (49/50) |
| card_lexical_template | 64/86 (30/50) | 74/86 (39/50), 1 short | 78/86 (42/50), 1 short | 81/86 (45/50), 1 short | 86/86 (50/50), 5 short |
| card_dense_summary | 59/86 (30/50) | 71/86 (38/50) | 78/86 (42/50) | 82/86 (46/50) | 85/86 (49/50) |
| card_lexical_summary | 61/86 (27/50) | 68/86 (34/50), 1 short | 71/86 (37/50), 1 short | 72/86 (38/50), 1 short | 84/86 (48/50), 5 short |
| graph_retrieval | 73/86 (39/50), 18 short | 81/86 (45/50), 43 short | 81/86 (45/50), 48 short | 82/86 (46/50), 49 short | 82/86 (46/50), 50 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | claim_dense over chunk_lexical: 3 / 1, p 0.625 | graph_retrieval over claim_lexical: 13 / 9, p 0.523 |
| 10 | claim_dense over graph_retrieval: 2 / 1, p 1.000 | graph_retrieval over card_dense_template: 8 / 5, p 0.581 |
| 15 | claim_dense over graph_retrieval: 2 / 1, p 1.000 | card_dense_template over graph_retrieval: 5 / 4, p 1.000 |
| 20 | claim_dense over claim_lexical: 1 / 0, p 1.000 | card_dense_template over card_dense_summary: 3 / 1, p 0.625 |
| 50 | card_dense_template over card_dense_summary: 0 / 0, p 1.000 | card_lexical_template over card_dense_template: 1 / 0, p 1.000 |

## heldout (graph bc7c5a1efe3d)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 26/28 (34/36) | 26/28 (34/36) | 26/28 (34/36) | 27/28 (35/36) | 28/28 (36/36) |
| chunk_lexical | 25/28 (32/36) | 25/28 (33/36) | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36), 3 short |
| claim_dense | 23/28 (31/36) | 24/28 (32/36) | 24/28 (32/36), 1 short | 25/28 (33/36), 6 short | 27/28 (35/36), 28 short |
| claim_lexical | 20/28 (27/36), 1 short | 21/28 (28/36), 2 short | 23/28 (31/36), 3 short | 23/28 (31/36), 8 short | 23/28 (31/36), 28 short |
| card_dense_template | 22/28 (30/36) | 24/28 (32/36) | 25/28 (33/36) | 25/28 (33/36) | 28/28 (36/36), 6 short |
| card_lexical_template | 25/28 (33/36) | 25/28 (33/36) | 27/28 (35/36) | 28/28 (36/36) | 28/28 (36/36), 1 short |
| card_dense_summary | 22/28 (30/36) | 23/28 (31/36) | 25/28 (33/36) | 25/28 (33/36) | 28/28 (36/36), 7 short |
| card_lexical_summary | 23/28 (31/36) | 25/28 (33/36) | 26/28 (34/36) | 27/28 (35/36) | 28/28 (36/36) |
| graph_retrieval | 26/28 (34/36), 1 short | 26/28 (34/36), 1 short | 27/28 (35/36), 2 short | 27/28 (35/36), 2 short | 28/28 (36/36), 4 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 41/65 (30/51) | 48/65 (37/51) | 57/65 (43/51) | 59/65 (45/51) | 65/65 (51/51) |
| chunk_lexical | 38/65 (26/51) | 46/65 (34/51) | 55/65 (41/51) | 57/65 (43/51) | 65/65 (51/51) |
| claim_dense | 45/65 (32/51) | 51/65 (38/51) | 53/65 (39/51) | 55/65 (41/51) | 57/65 (43/51) |
| claim_lexical | 40/65 (30/51) | 47/65 (35/51), 1 short | 52/65 (39/51), 2 short | 53/65 (40/51), 2 short | 56/65 (42/51), 3 short |
| card_dense_template | 53/65 (40/51) | 55/65 (41/51) | 57/65 (43/51) | 58/65 (44/51) | 62/65 (48/51) |
| card_lexical_template | 55/65 (41/51) | 60/65 (46/51) | 60/65 (46/51) | 62/65 (48/51) | 64/65 (50/51), 2 short |
| card_dense_summary | 49/65 (36/51) | 55/65 (41/51) | 56/65 (42/51) | 60/65 (46/51) | 63/65 (49/51) |
| card_lexical_summary | 56/65 (42/51) | 58/65 (44/51) | 58/65 (44/51) | 58/65 (44/51) | 63/65 (49/51), 1 short |
| graph_retrieval | 46/65 (33/51), 5 short | 58/65 (44/51), 34 short | 61/65 (47/51), 50 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_dense over graph_retrieval: 0 / 0, p 1.000 | card_lexical_summary over card_lexical_template: 2 / 1, p 1.000 |
| 10 | chunk_dense over graph_retrieval: 0 / 0, p 1.000 | card_lexical_template over card_lexical_summary: 2 / 0, p 0.500 |
| 15 | card_lexical_template over graph_retrieval: 1 / 1, p 1.000 | graph_retrieval over card_lexical_template: 3 / 2, p 1.000 |
| 20 | card_lexical_template over chunk_dense: 1 / 0, p 1.000 | card_lexical_template over graph_retrieval: 3 / 2, p 1.000 |
| 50 | chunk_dense over card_dense_template: 0 / 0, p 1.000 | chunk_dense over chunk_lexical: 0 / 0, p 1.000 |

## generality (graph f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 34/38 (44/51) | 37/38 (49/51) | 38/38 (51/51) | 38/38 (51/51) | 38/38 (51/51), 38 short |
| chunk_lexical | 37/38 (47/51) | 37/38 (48/51), 2 short | 38/38 (51/51), 11 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| claim_dense | 33/38 (44/51) | 37/38 (50/51), 5 short | 38/38 (51/51), 17 short | 38/38 (51/51), 35 short | 38/38 (51/51), 38 short |
| claim_lexical | 30/38 (41/51), 9 short | 34/38 (47/51), 17 short | 35/38 (48/51), 32 short | 35/38 (48/51), 35 short | 35/38 (48/51), 38 short |
| card_dense_template | 31/38 (40/51) | 34/38 (47/51) | 37/38 (50/51), 7 short | 37/38 (50/51), 20 short | 38/38 (51/51), 38 short |
| card_lexical_template | 32/38 (43/51), 1 short | 36/38 (49/51), 8 short | 37/38 (50/51), 14 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| card_dense_summary | 31/38 (41/51) | 34/38 (47/51) | 35/38 (48/51), 8 short | 37/38 (50/51), 18 short | 37/38 (50/51), 38 short |
| card_lexical_summary | 31/38 (42/51), 2 short | 35/38 (48/51), 6 short | 37/38 (50/51), 12 short | 38/38 (51/51), 20 short | 38/38 (51/51), 38 short |
| graph_retrieval | 34/38 (44/51), 10 short | 36/38 (49/51), 29 short | 36/38 (49/51), 38 short | 36/38 (49/51), 38 short | 36/38 (49/51), 38 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 37/62 (16/40) | 46/62 (25/40) | 50/62 (29/40) | 55/62 (34/40) | 59/62 (37/40) |
| chunk_lexical | 31/62 (17/40) | 47/62 (26/40) | 48/62 (27/40) | 55/62 (34/40) | 60/62 (38/40) |
| claim_dense | 41/62 (19/40) | 43/62 (21/40) | 44/62 (22/40) | 46/62 (24/40) | 47/62 (25/40), 15 short |
| claim_lexical | 37/62 (16/40), 1 short | 42/62 (21/40), 6 short | 45/62 (23/40), 9 short | 45/62 (23/40), 13 short | 45/62 (23/40), 36 short |
| card_dense_template | 40/62 (20/40) | 47/62 (25/40) | 52/62 (30/40) | 53/62 (31/40) | 54/62 (32/40) |
| card_lexical_template | 45/62 (26/40) | 51/62 (30/40), 2 short | 56/62 (34/40), 5 short | 58/62 (36/40), 8 short | 58/62 (36/40), 20 short |
| card_dense_summary | 39/62 (21/40) | 51/62 (30/40) | 55/62 (33/40) | 57/62 (35/40) | 58/62 (36/40) |
| card_lexical_summary | 36/62 (18/40) | 48/62 (27/40), 2 short | 54/62 (32/40), 7 short | 55/62 (33/40), 9 short | 58/62 (36/40), 21 short |
| graph_retrieval | 41/62 (22/40), 10 short | 48/62 (27/40), 36 short | 48/62 (27/40), 40 short | 48/62 (27/40), 40 short | 48/62 (27/40), 40 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_lexical over chunk_dense: 3 / 0, p 0.250 | card_lexical_template over graph_retrieval: 7 / 3, p 0.344 |
| 10 | claim_dense over chunk_dense: 1 / 1, p 1.000 | card_lexical_template over card_dense_summary: 5 / 5, p 1.000 |
| 15 | chunk_dense over chunk_lexical: 0 / 0, p 1.000 | card_lexical_template over card_dense_summary: 2 / 1, p 1.000 |
| 20 | chunk_dense over chunk_lexical: 0 / 0, p 1.000 | card_lexical_template over card_dense_summary: 1 / 0, p 1.000 |
| 50 | chunk_dense over chunk_lexical: 0 / 0, p 1.000 | chunk_lexical over chunk_dense: 1 / 0, p 1.000 |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K (Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 81/99 (119/160) | 89/99 (136/160) | 91/99 (144/160) | 93/99 (148/160) | 97/99 (158/160), 38 short |
| chunk_lexical | 86/99 (128/160) | 88/99 (140/160), 2 short | 90/99 (149/160), 11 short | 91/99 (150/160), 23 short | 95/99 (155/160), 71 short |
| claim_dense | 82/99 (124/160) | 90/99 (142/160), 9 short | 92/99 (144/160), 28 short | 93/99 (147/160), 56 short | 95/99 (151/160), 99 short |
| claim_lexical | 74/99 (114/160), 11 short | 82/99 (127/160), 25 short | 86/99 (137/160), 47 short | 87/99 (140/160), 59 short | 87/99 (141/160), 99 short |
| card_dense_template | 73/99 (115/160) | 83/99 (132/160) | 87/99 (137/160), 7 short | 87/99 (140/160), 20 short | 98/99 (157/160), 66 short |
| card_lexical_template | 79/99 (122/160), 1 short | 87/99 (135/160), 9 short | 90/99 (139/160), 15 short | 92/99 (145/160), 21 short | 97/99 (157/160), 62 short |
| card_dense_summary | 74/99 (113/160) | 82/99 (130/160) | 87/99 (141/160), 8 short | 90/99 (145/160), 18 short | 97/99 (154/160), 67 short |
| card_lexical_summary | 75/99 (117/160), 2 short | 86/99 (135/160), 7 short | 89/99 (138/160), 13 short | 92/99 (141/160), 21 short | 97/99 (156/160), 54 short |
| graph_retrieval | 84/99 (125/160), 13 short | 90/99 (141/160), 43 short | 92/99 (143/160), 62 short | 92/99 (143/160), 66 short | 93/99 (144/160), 75 short |

**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds.

| Technique | K = 5 | K = 10 | K = 15 | K = 20 | K = 50 |
|---|---|---|---|---|---|
| chunk_dense | 112/213 (55/141) | 140/213 (79/141) | 161/213 (93/141) | 174/213 (106/141) | 188/213 (117/141) |
| chunk_lexical | 102/213 (53/141) | 141/213 (78/141) | 162/213 (93/141) | 178/213 (108/141) | 200/213 (128/141) |
| claim_dense | 151/213 (82/141) | 162/213 (92/141) | 167/213 (96/141) | 171/213 (100/141) | 178/213 (107/141), 40 short |
| claim_lexical | 147/213 (81/141), 1 short | 160/213 (92/141), 8 short | 169/213 (99/141), 12 short | 171/213 (101/141), 16 short | 175/213 (104/141), 63 short |
| card_dense_template | 161/213 (94/141) | 178/213 (108/141) | 191/213 (119/141) | 195/213 (123/141) | 201/213 (129/141) |
| card_lexical_template | 164/213 (97/141) | 185/213 (115/141), 3 short | 194/213 (122/141), 6 short | 201/213 (129/141), 9 short | 208/213 (136/141), 27 short |
| card_dense_summary | 147/213 (87/141) | 177/213 (109/141) | 189/213 (117/141) | 199/213 (127/141) | 206/213 (134/141) |
| card_lexical_summary | 153/213 (87/141) | 174/213 (105/141), 3 short | 183/213 (113/141), 8 short | 185/213 (115/141), 10 short | 205/213 (133/141), 27 short |
| graph_retrieval | 160/213 (94/141), 33 short | 187/213 (116/141), 113 short | 190/213 (119/141), 138 short | 191/213 (120/141), 140 short | 191/213 (120/141), 141 short |

**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the runner-up complete (evidence) or find every target of (start nodes), exact McNemar p.

| K | Evidence | Start nodes |
|---|---|---|
| 5 | chunk_lexical over graph_retrieval: 7 / 5, p 0.774 | card_lexical_template over card_dense_template: 18 / 15, p 0.728 |
| 10 | claim_dense over graph_retrieval: 5 / 5, p 1.000 | graph_retrieval over card_lexical_template: 17 / 16, p 1.000 |
| 15 | claim_dense over graph_retrieval: 5 / 5, p 1.000 | card_lexical_template over card_dense_template: 13 / 10, p 0.678 |
| 20 | chunk_dense over claim_dense: 3 / 3, p 1.000 | card_lexical_template over card_dense_summary: 7 / 5, p 0.774 |
| 50 | card_dense_template over chunk_dense: 2 / 1, p 1.000 | card_lexical_template over card_dense_summary: 2 / 0, p 0.500 |
