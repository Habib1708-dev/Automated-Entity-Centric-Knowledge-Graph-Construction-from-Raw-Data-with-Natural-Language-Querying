## furniture (graph 392a170ecc10)

Targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds. Role: *choice* competes under the pre-registered rule, *reference* (interleaving) and *record* (summary cards) are scored beside it, *baseline* is one card list alone.

| Setting | Role | K = 5 | K = 10 | K = 15 | K = 20 | K = 25 | K = 30 | K = 50 |
|---|---|---|---|---|---|---|---|---|
| card_lexical_template | baseline | 64/86 (30/50) | 74/86 (39/50), 1 short | 78/86 (42/50), 1 short | 81/86 (45/50), 1 short | 85/86 (49/50), 1 short | 85/86 (49/50), 1 short | 86/86 (50/50), 5 short |
| card_dense_template | baseline | 68/86 (34/50) | 76/86 (42/50) | 82/86 (46/50) | 84/86 (48/50) | 84/86 (48/50) | 85/86 (49/50) | 85/86 (49/50) |
| rrf60_c10_template | choice | 71/86 (37/50) | 82/86 (46/50) | 83/86 (47/50), 18 short | 83/86 (47/50), 49 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short |
| rrf10_c10_template | choice | 71/86 (37/50) | 82/86 (46/50) | 83/86 (47/50), 18 short | 83/86 (47/50), 49 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short |
| interleave_c10_template | reference | 70/86 (34/50) | 83/86 (47/50) | 83/86 (47/50), 18 short | 83/86 (47/50), 49 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short | 83/86 (47/50), 50 short |
| rrf60_c25_template | choice | 73/86 (37/50) | 81/86 (45/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) | 86/86 (50/50), 2 short | 86/86 (50/50), 50 short |
| rrf10_c25_template | choice | 73/86 (37/50) | 82/86 (46/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) | 86/86 (50/50), 2 short | 86/86 (50/50), 50 short |
| interleave_c25_template | reference | 70/86 (34/50) | 83/86 (47/50) | 83/86 (47/50) | 83/86 (47/50) | 85/86 (49/50) | 85/86 (49/50), 2 short | 86/86 (50/50), 50 short |
| rrf60_c50_template | choice | 73/86 (37/50) | 81/86 (45/50) | 85/86 (49/50) | 85/86 (49/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) |
| rrf10_c50_template | choice | 73/86 (37/50) | 81/86 (45/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) | 86/86 (50/50) | 86/86 (50/50) |
| interleave_c50_template | reference | 70/86 (34/50) | 83/86 (47/50) | 83/86 (47/50) | 83/86 (47/50) | 85/86 (49/50) | 85/86 (49/50) | 86/86 (50/50) |
| rerank_c25_template | choice | 81/86 (45/50) | 84/86 (48/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) | 86/86 (50/50), 2 short | 86/86 (50/50), 50 short |
| rerank_c50_template | choice | 79/86 (43/50) | 84/86 (48/50) | 85/86 (49/50) | 85/86 (49/50) | 85/86 (49/50) | 86/86 (50/50) | 86/86 (50/50) |
| rrf60_c10_summary | record | 62/86 (31/50) | 74/86 (39/50) | 79/86 (43/50), 12 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short |
| rrf10_c10_summary | record | 62/86 (31/50) | 74/86 (39/50) | 79/86 (43/50), 12 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short |
| interleave_c10_summary | record | 62/86 (28/50) | 73/86 (38/50) | 79/86 (43/50), 12 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short | 79/86 (43/50), 50 short |
| rrf60_c25_summary | record | 65/86 (33/50) | 69/86 (36/50) | 71/86 (38/50) | 78/86 (42/50) | 83/86 (47/50) | 85/86 (49/50), 1 short | 86/86 (50/50), 50 short |
| rrf10_c25_summary | record | 68/86 (35/50) | 69/86 (36/50) | 73/86 (38/50) | 79/86 (43/50) | 83/86 (47/50) | 85/86 (49/50), 1 short | 86/86 (50/50), 50 short |
| interleave_c25_summary | record | 62/86 (28/50) | 73/86 (38/50) | 79/86 (43/50) | 82/86 (46/50) | 84/86 (48/50) | 85/86 (49/50), 1 short | 86/86 (50/50), 50 short |
| rrf60_c50_summary | record | 65/86 (33/50) | 70/86 (36/50) | 73/86 (37/50) | 80/86 (44/50) | 81/86 (45/50) | 84/86 (48/50) | 86/86 (50/50) |
| rrf10_c50_summary | record | 68/86 (35/50) | 73/86 (38/50) | 76/86 (40/50) | 79/86 (43/50) | 82/86 (46/50) | 84/86 (48/50) | 86/86 (50/50) |
| interleave_c50_summary | record | 62/86 (28/50) | 73/86 (38/50) | 79/86 (43/50) | 82/86 (46/50) | 84/86 (48/50) | 85/86 (49/50) | 86/86 (50/50) |

## heldout (graph bc7c5a1efe3d)

Targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds. Role: *choice* competes under the pre-registered rule, *reference* (interleaving) and *record* (summary cards) are scored beside it, *baseline* is one card list alone.

| Setting | Role | K = 5 | K = 10 | K = 15 | K = 20 | K = 25 | K = 30 | K = 50 |
|---|---|---|---|---|---|---|---|---|
| card_lexical_template | baseline | 55/65 (41/51) | 60/65 (46/51) | 60/65 (46/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 64/65 (50/51), 2 short |
| card_dense_template | baseline | 53/65 (40/51) | 55/65 (41/51) | 57/65 (43/51) | 58/65 (44/51) | 60/65 (46/51) | 61/65 (47/51) | 62/65 (48/51) |
| rrf60_c10_template | choice | 54/65 (41/51) | 62/65 (48/51) | 62/65 (48/51), 20 short | 62/65 (48/51), 49 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short |
| rrf10_c10_template | choice | 54/65 (41/51) | 62/65 (48/51) | 62/65 (48/51), 20 short | 62/65 (48/51), 49 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short |
| interleave_c10_template | reference | 56/65 (42/51) | 62/65 (48/51) | 62/65 (48/51), 20 short | 62/65 (48/51), 49 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short | 62/65 (48/51), 51 short |
| rrf60_c25_template | choice | 56/65 (42/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51), 51 short |
| rrf10_c25_template | choice | 57/65 (43/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51), 51 short |
| interleave_c25_template | reference | 56/65 (42/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51), 51 short |
| rrf60_c50_template | choice | 57/65 (43/51) | 61/65 (47/51) | 61/65 (47/51) | 61/65 (47/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) |
| rrf10_c50_template | choice | 58/65 (44/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 63/65 (49/51) |
| interleave_c50_template | reference | 56/65 (42/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) |
| rerank_c25_template | choice | 58/65 (44/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51), 51 short |
| rerank_c50_template | choice | 58/65 (44/51) | 60/65 (46/51) | 62/65 (48/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 63/65 (49/51) |
| rrf60_c10_summary | record | 54/65 (41/51) | 59/65 (45/51) | 60/65 (46/51), 20 short | 61/65 (47/51), 46 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short |
| rrf10_c10_summary | record | 54/65 (41/51) | 59/65 (45/51) | 60/65 (46/51), 20 short | 61/65 (47/51), 46 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short |
| interleave_c10_summary | record | 53/65 (39/51) | 59/65 (45/51) | 60/65 (46/51), 20 short | 61/65 (47/51), 46 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short | 61/65 (47/51), 51 short |
| rrf60_c25_summary | record | 54/65 (41/51) | 58/65 (44/51) | 60/65 (46/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 64/65 (50/51), 48 short |
| rrf10_c25_summary | record | 54/65 (40/51) | 58/65 (44/51) | 60/65 (46/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 64/65 (50/51), 48 short |
| interleave_c25_summary | record | 53/65 (39/51) | 59/65 (45/51) | 60/65 (46/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 64/65 (50/51), 48 short |
| rrf60_c50_summary | record | 55/65 (42/51) | 61/65 (47/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 63/65 (49/51) | 64/65 (50/51) |
| rrf10_c50_summary | record | 54/65 (40/51) | 60/65 (46/51) | 62/65 (48/51) | 63/65 (49/51) | 63/65 (49/51) | 63/65 (49/51) | 64/65 (50/51) |
| interleave_c50_summary | record | 53/65 (39/51) | 59/65 (45/51) | 60/65 (46/51) | 61/65 (47/51) | 62/65 (48/51) | 62/65 (48/51) | 64/65 (50/51) |

## generality (graph f79f9411ea81)

Targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds. Role: *choice* competes under the pre-registered rule, *reference* (interleaving) and *record* (summary cards) are scored beside it, *baseline* is one card list alone.

| Setting | Role | K = 5 | K = 10 | K = 15 | K = 20 | K = 25 | K = 30 | K = 50 |
|---|---|---|---|---|---|---|---|---|
| card_lexical_template | baseline | 45/62 (26/40) | 51/62 (30/40), 2 short | 56/62 (34/40), 5 short | 58/62 (36/40), 8 short | 58/62 (36/40), 13 short | 58/62 (36/40), 13 short | 58/62 (36/40), 20 short |
| card_dense_template | baseline | 40/62 (20/40) | 47/62 (25/40) | 52/62 (30/40) | 53/62 (31/40) | 53/62 (31/40) | 54/62 (32/40) | 54/62 (32/40) |
| rrf60_c10_template | choice | 46/62 (26/40) | 51/62 (29/40) | 54/62 (32/40), 23 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short |
| rrf10_c10_template | choice | 46/62 (26/40) | 51/62 (29/40) | 54/62 (32/40), 23 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short |
| interleave_c10_template | reference | 44/62 (24/40) | 49/62 (29/40) | 54/62 (32/40), 23 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short | 55/62 (33/40), 40 short |
| rrf60_c25_template | choice | 42/62 (22/40) | 52/62 (30/40) | 56/62 (34/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| rrf10_c25_template | choice | 42/62 (22/40) | 54/62 (32/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| interleave_c25_template | reference | 44/62 (24/40) | 49/62 (29/40) | 54/62 (32/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| rrf60_c50_template | choice | 42/62 (22/40) | 53/62 (31/40) | 54/62 (32/40) | 56/62 (34/40) | 56/62 (34/40) | 56/62 (34/40) | 58/62 (36/40) |
| rrf10_c50_template | choice | 42/62 (22/40) | 53/62 (31/40) | 55/62 (33/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) |
| interleave_c50_template | reference | 44/62 (24/40) | 49/62 (29/40) | 54/62 (32/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) |
| rerank_c25_template | choice | 50/62 (29/40) | 53/62 (31/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| rerank_c50_template | choice | 48/62 (27/40) | 52/62 (31/40) | 55/62 (33/40) | 55/62 (33/40) | 55/62 (33/40) | 56/62 (34/40) | 58/62 (36/40) |
| rrf60_c10_summary | record | 42/62 (23/40) | 52/62 (32/40) | 56/62 (34/40), 25 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short |
| rrf10_c10_summary | record | 42/62 (23/40) | 52/62 (32/40) | 56/62 (34/40), 25 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short |
| interleave_c10_summary | record | 42/62 (22/40) | 51/62 (31/40) | 56/62 (34/40), 25 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short | 57/62 (35/40), 40 short |
| rrf60_c25_summary | record | 43/62 (24/40) | 53/62 (31/40) | 56/62 (34/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| rrf10_c25_summary | record | 42/62 (23/40) | 53/62 (31/40) | 55/62 (33/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| interleave_c25_summary | record | 42/62 (22/40) | 51/62 (31/40) | 56/62 (34/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40), 11 short | 58/62 (36/40), 40 short |
| rrf60_c50_summary | record | 43/62 (24/40) | 53/62 (31/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) |
| rrf10_c50_summary | record | 42/62 (23/40) | 53/62 (32/40) | 57/62 (35/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) |
| interleave_c50_summary | record | 42/62 (22/40) | 51/62 (31/40) | 56/62 (34/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) | 58/62 (36/40) |

## pooled (graph 392a170ecc10+bc7c5a1efe3d+f79f9411ea81)

Targets with a node among the top K seeds (Seed Recall@K), then questions with every target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds. Role: *choice* competes under the pre-registered rule, *reference* (interleaving) and *record* (summary cards) are scored beside it, *baseline* is one card list alone.

| Setting | Role | K = 5 | K = 10 | K = 15 | K = 20 | K = 25 | K = 30 | K = 50 |
|---|---|---|---|---|---|---|---|---|
| card_lexical_template | baseline | 164/213 (97/141) | 185/213 (115/141), 3 short | 194/213 (122/141), 6 short | 201/213 (129/141), 9 short | 205/213 (133/141), 14 short | 205/213 (133/141), 14 short | 208/213 (136/141), 27 short |
| card_dense_template | baseline | 161/213 (94/141) | 178/213 (108/141) | 191/213 (119/141) | 195/213 (123/141) | 197/213 (125/141) | 200/213 (128/141) | 201/213 (129/141) |
| rrf60_c10_template | choice | 171/213 (104/141) | 195/213 (123/141) | 199/213 (127/141), 61 short | 200/213 (128/141), 138 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short |
| rrf10_c10_template | choice | 171/213 (104/141) | 195/213 (123/141) | 199/213 (127/141), 61 short | 200/213 (128/141), 138 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short |
| interleave_c10_template | reference | 170/213 (100/141) | 194/213 (124/141) | 199/213 (127/141), 61 short | 200/213 (128/141), 138 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short | 200/213 (128/141), 141 short |
| rrf60_c25_template | choice | 171/213 (101/141) | 194/213 (122/141) | 203/213 (131/141) | 205/213 (133/141) | 206/213 (134/141) | 207/213 (135/141), 13 short | 207/213 (135/141), 141 short |
| rrf10_c25_template | choice | 172/213 (102/141) | 197/213 (125/141) | 204/213 (132/141) | 206/213 (134/141) | 206/213 (134/141) | 207/213 (135/141), 13 short | 207/213 (135/141), 141 short |
| interleave_c25_template | reference | 170/213 (100/141) | 194/213 (124/141) | 199/213 (127/141) | 202/213 (130/141) | 205/213 (133/141) | 206/213 (134/141), 13 short | 207/213 (135/141), 141 short |
| rrf60_c50_template | choice | 172/213 (102/141) | 195/213 (123/141) | 200/213 (128/141) | 202/213 (130/141) | 203/213 (131/141) | 205/213 (133/141) | 207/213 (135/141) |
| rrf10_c50_template | choice | 173/213 (103/141) | 195/213 (123/141) | 202/213 (130/141) | 206/213 (134/141) | 207/213 (135/141) | 207/213 (135/141) | 207/213 (135/141) |
| interleave_c50_template | reference | 170/213 (100/141) | 194/213 (124/141) | 199/213 (127/141) | 202/213 (130/141) | 205/213 (133/141) | 206/213 (134/141) | 207/213 (135/141) |
| rerank_c25_template | choice | 189/213 (118/141) | 198/213 (126/141) | 204/213 (132/141) | 206/213 (134/141) | 206/213 (134/141) | 207/213 (135/141), 13 short | 207/213 (135/141), 141 short |
| rerank_c50_template | choice | 185/213 (114/141) | 196/213 (125/141) | 202/213 (130/141) | 202/213 (130/141) | 203/213 (131/141) | 205/213 (133/141) | 207/213 (135/141) |
| rrf60_c10_summary | record | 158/213 (95/141) | 185/213 (116/141) | 195/213 (123/141), 57 short | 197/213 (125/141), 136 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short |
| rrf10_c10_summary | record | 158/213 (95/141) | 185/213 (116/141) | 195/213 (123/141), 57 short | 197/213 (125/141), 136 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short |
| interleave_c10_summary | record | 157/213 (89/141) | 183/213 (114/141) | 195/213 (123/141), 57 short | 197/213 (125/141), 136 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short | 197/213 (125/141), 141 short |
| rrf60_c25_summary | record | 162/213 (98/141) | 180/213 (111/141) | 187/213 (118/141) | 197/213 (125/141) | 204/213 (132/141) | 206/213 (134/141), 12 short | 208/213 (136/141), 138 short |
| rrf10_c25_summary | record | 164/213 (98/141) | 180/213 (111/141) | 188/213 (117/141) | 198/213 (126/141) | 204/213 (132/141) | 206/213 (134/141), 12 short | 208/213 (136/141), 138 short |
| interleave_c25_summary | record | 157/213 (89/141) | 183/213 (114/141) | 195/213 (123/141) | 201/213 (129/141) | 204/213 (132/141) | 205/213 (133/141), 12 short | 208/213 (136/141), 138 short |
| rrf60_c50_summary | record | 163/213 (99/141) | 184/213 (114/141) | 192/213 (120/141) | 201/213 (129/141) | 202/213 (130/141) | 205/213 (133/141) | 208/213 (136/141) |
| rrf10_c50_summary | record | 164/213 (98/141) | 186/213 (116/141) | 195/213 (123/141) | 200/213 (128/141) | 203/213 (131/141) | 205/213 (133/141) | 208/213 (136/141) |
| interleave_c50_summary | record | 157/213 (89/141) | 183/213 (114/141) | 195/213 (123/141) | 201/213 (129/141) | 204/213 (132/141) | 205/213 (133/141) | 208/213 (136/141) |

## The choice (pre-registered rule)

- Chosen on furniture, confirmed on heldout, generality.
- Best at K = 20: rrf60_c25_template; best free setting: rrf60_c25_template.
- **Chosen: rrf60_c25_template, K = 15.** The best setting at K = 20 on the choice dataset is free.

Questions with every target found only by the first / only by the second row, exact McNemar p:

| Dataset | First | Second | K | Only first | Only second | p |
|---|---|---|---|---|---|---|
| furniture | card_dense_template | card_lexical_template | 15 | 7 | 3 | 0.344 |
| heldout | card_dense_template | card_lexical_template | 15 | 2 | 5 | 0.453 |
| generality | card_dense_template | card_lexical_template | 15 | 1 | 5 | 0.219 |
| furniture | rrf60_c10_template | card_lexical_template | 15 | 5 | 0 | 0.062 |
| heldout | rrf60_c10_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf60_c10_template | card_lexical_template | 15 | 0 | 2 | 0.500 |
| furniture | rrf10_c10_template | card_lexical_template | 15 | 5 | 0 | 0.062 |
| heldout | rrf10_c10_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf10_c10_template | card_lexical_template | 15 | 0 | 2 | 0.500 |
| furniture | interleave_c10_template | card_lexical_template | 15 | 5 | 0 | 0.062 |
| heldout | interleave_c10_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | interleave_c10_template | card_lexical_template | 15 | 0 | 2 | 0.500 |
| furniture | rrf60_c25_template | card_lexical_template | 15 | 8 | 1 | 0.039 |
| heldout | rrf60_c25_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf60_c25_template | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | rrf10_c25_template | card_lexical_template | 15 | 8 | 1 | 0.039 |
| heldout | rrf10_c25_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf10_c25_template | card_lexical_template | 15 | 1 | 0 | 1.000 |
| furniture | interleave_c25_template | card_lexical_template | 15 | 5 | 0 | 0.062 |
| heldout | interleave_c25_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | interleave_c25_template | card_lexical_template | 15 | 0 | 2 | 0.500 |
| furniture | rrf60_c50_template | card_lexical_template | 15 | 8 | 1 | 0.039 |
| heldout | rrf60_c50_template | card_lexical_template | 15 | 2 | 1 | 1.000 |
| generality | rrf60_c50_template | card_lexical_template | 15 | 1 | 3 | 0.625 |
| furniture | rrf10_c50_template | card_lexical_template | 15 | 7 | 0 | 0.016 |
| heldout | rrf10_c50_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf10_c50_template | card_lexical_template | 15 | 0 | 1 | 1.000 |
| furniture | interleave_c50_template | card_lexical_template | 15 | 5 | 0 | 0.062 |
| heldout | interleave_c50_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | interleave_c50_template | card_lexical_template | 15 | 0 | 2 | 0.500 |
| furniture | rerank_c25_template | card_lexical_template | 15 | 8 | 1 | 0.039 |
| heldout | rerank_c25_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rerank_c25_template | card_lexical_template | 15 | 2 | 1 | 1.000 |
| furniture | rerank_c50_template | card_lexical_template | 15 | 8 | 1 | 0.039 |
| heldout | rerank_c50_template | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rerank_c50_template | card_lexical_template | 15 | 2 | 3 | 1.000 |
| furniture | rrf60_c10_summary | card_lexical_template | 15 | 3 | 2 | 1.000 |
| heldout | rrf60_c10_summary | card_lexical_template | 15 | 2 | 2 | 1.000 |
| generality | rrf60_c10_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | rrf10_c10_summary | card_lexical_template | 15 | 3 | 2 | 1.000 |
| heldout | rrf10_c10_summary | card_lexical_template | 15 | 2 | 2 | 1.000 |
| generality | rrf10_c10_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | interleave_c10_summary | card_lexical_template | 15 | 3 | 2 | 1.000 |
| heldout | interleave_c10_summary | card_lexical_template | 15 | 2 | 2 | 1.000 |
| generality | interleave_c10_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | rrf60_c25_summary | card_lexical_template | 15 | 2 | 6 | 0.289 |
| heldout | rrf60_c25_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| generality | rrf60_c25_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | rrf10_c25_summary | card_lexical_template | 15 | 2 | 6 | 0.289 |
| heldout | rrf10_c25_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| generality | rrf10_c25_summary | card_lexical_template | 15 | 1 | 2 | 1.000 |
| furniture | interleave_c25_summary | card_lexical_template | 15 | 3 | 2 | 1.000 |
| heldout | interleave_c25_summary | card_lexical_template | 15 | 2 | 2 | 1.000 |
| generality | interleave_c25_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| furniture | rrf60_c50_summary | card_lexical_template | 15 | 1 | 6 | 0.125 |
| heldout | rrf60_c50_summary | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf60_c50_summary | card_lexical_template | 15 | 1 | 0 | 1.000 |
| furniture | rrf10_c50_summary | card_lexical_template | 15 | 1 | 3 | 0.625 |
| heldout | rrf10_c50_summary | card_lexical_template | 15 | 2 | 0 | 0.500 |
| generality | rrf10_c50_summary | card_lexical_template | 15 | 1 | 0 | 1.000 |
| furniture | interleave_c50_summary | card_lexical_template | 15 | 3 | 2 | 1.000 |
| heldout | interleave_c50_summary | card_lexical_template | 15 | 2 | 2 | 1.000 |
| generality | interleave_c50_summary | card_lexical_template | 15 | 1 | 1 | 1.000 |
| pooled | card_dense_template | card_lexical_template | 15 | 10 | 13 | 0.678 |
| pooled | rrf60_c10_template | card_lexical_template | 15 | 7 | 2 | 0.180 |
| pooled | rrf10_c10_template | card_lexical_template | 15 | 7 | 2 | 0.180 |
| pooled | interleave_c10_template | card_lexical_template | 15 | 7 | 2 | 0.180 |
| pooled | rrf60_c25_template | card_lexical_template | 15 | 11 | 2 | 0.022 |
| pooled | rrf10_c25_template | card_lexical_template | 15 | 11 | 1 | 0.006 |
| pooled | interleave_c25_template | card_lexical_template | 15 | 7 | 2 | 0.180 |
| pooled | rrf60_c50_template | card_lexical_template | 15 | 11 | 5 | 0.210 |
| pooled | rrf10_c50_template | card_lexical_template | 15 | 9 | 1 | 0.021 |
| pooled | interleave_c50_template | card_lexical_template | 15 | 7 | 2 | 0.180 |
| pooled | rerank_c25_template | card_lexical_template | 15 | 12 | 2 | 0.013 |
| pooled | rerank_c50_template | card_lexical_template | 15 | 12 | 4 | 0.077 |
| pooled | rrf60_c10_summary | card_lexical_template | 15 | 6 | 5 | 1.000 |
| pooled | rrf10_c10_summary | card_lexical_template | 15 | 6 | 5 | 1.000 |
| pooled | interleave_c10_summary | card_lexical_template | 15 | 6 | 5 | 1.000 |
| pooled | rrf60_c25_summary | card_lexical_template | 15 | 4 | 8 | 0.388 |
| pooled | rrf10_c25_summary | card_lexical_template | 15 | 4 | 9 | 0.267 |
| pooled | interleave_c25_summary | card_lexical_template | 15 | 6 | 5 | 1.000 |
| pooled | rrf60_c50_summary | card_lexical_template | 15 | 4 | 6 | 0.754 |
| pooled | rrf10_c50_summary | card_lexical_template | 15 | 4 | 3 | 1.000 |
| pooled | interleave_c50_summary | card_lexical_template | 15 | 6 | 5 | 1.000 |
