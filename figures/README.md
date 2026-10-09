# Figures

The thesis figures, each drawn by code from a committed record and explained in its folder's `README.md`.
Rebuild all of them with `uv run python -m figures.scripts.build`. The rules for adding a figure are in
[CLAUDE.md](CLAUDE.md).

## Results (charts of measured numbers)

### Retrieval: [r128_techniques](results/retrieval/r128_techniques/README.md), each technique alone, K = 5 to 50

| Figure | Shows |
|---|---|
| [r128_fig1_evidence_vs_start_nodes](results/retrieval/r128_techniques/r128_fig1_evidence_vs_start_nodes.png) | each technique's evidence against start nodes at K = 5 and 20 (scatter, Wilson intervals) |
| [r128_fig2_recall_by_k](results/retrieval/r128_techniques/r128_fig2_recall_by_k.png) | recall against K per family, the others in gray (small multiples) |
| [r128_fig3_per_dataset_heatmaps](results/retrieval/r128_techniques/r128_fig3_per_dataset_heatmaps.png) | every technique x K on each dataset (heatmaps with counts) |
| [r128_fig4_paired_card_vs_text](results/retrieval/r128_techniques/r128_fig4_paired_card_vs_text.png) | questions only cards or only text get right, with McNemar p (diverging bars) |
| [r128_fig5_short_lists](results/retrieval/r128_techniques/r128_fig5_short_lists.png) | the share of lists shorter than K (lines) |

## Diagrams (how methods work)

### Retrieval: [r126_r128](diagrams/retrieval/r126_r128/README.md)

| Diagram | Shows |
|---|---|
| [r126_diag1_seed_derivation](diagrams/retrieval/r126_r128/r126_diag1_seed_derivation.png) | how chunk and claim retrieval name start nodes |
| [r128_diag2_experiment_design](diagrams/retrieval/r126_r128/r128_diag2_experiment_design.png) | the R128 experiment end to end |
