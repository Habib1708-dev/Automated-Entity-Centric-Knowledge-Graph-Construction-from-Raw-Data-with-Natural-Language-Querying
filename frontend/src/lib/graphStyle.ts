// The graph's Cytoscape stylesheet (R223), built from the page's CSS tokens (index.css) so the canvas follows
// the light or dark theme with the rest of the page. Kinds differ by shape as well as tone: a table record is
// a rounded square, an individual from the text a circle, a concept a diamond, and a mention no identity
// covers a dashed circle, so no kind is told by colour alone.
import type cytoscape from 'cytoscape'

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export const KIND_SHAPE = {
  record: 'round-rectangle',
  individual: 'ellipse',
  concept: 'diamond',
  mention: 'ellipse',
} as const

export function graphStyle(): cytoscape.StylesheetJson {
  const kinds = (['record', 'individual', 'concept', 'mention'] as const).map((kind) => ({
    selector: `node[kind = "${kind}"]`,
    style: { shape: KIND_SHAPE[kind], 'background-color': token(`--kind-${kind}`) },
  }))
  return [
    {
      selector: 'node',
      style: {
        width: 'data(size)',
        height: 'data(size)',
        label: 'data(label)',
        color: token('--text'),
        'font-family': 'Inter Variable, system-ui, sans-serif',
        'font-size': 15,
        'text-valign': 'bottom',
        'text-margin-y': 6,
        'text-wrap': 'ellipsis',
        'text-max-width': '160px',
        'text-background-color': token('--bg'),
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'border-width': 0,
        'transition-property': 'opacity',
        'transition-duration': 200,
      },
    },
    ...kinds,
    { selector: 'node[kind = "mention"]', style: { 'border-width': 2, 'border-style': 'dashed', 'border-color': token('--text-3') } },
    {
      selector: 'edge',
      style: {
        width: 'mapData(count, 1, 12, 1.5, 5)',
        'line-color': token('--edge'),
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': token('--edge'),
        'arrow-scale': 0.8,
        opacity: 0.9,
      },
    },
    { selector: 'edge[origin = "text"]', style: { 'line-color': token('--edge-text'), 'target-arrow-color': token('--edge-text'), opacity: 0.55 } },
    { selector: 'node.hover, node:selected', style: { label: 'data(name)', 'z-index': 10 } },
    { selector: 'node.picked', style: { label: 'data(name)', 'z-index': 10, 'border-width': 5, 'border-color': token('--accent'), 'border-style': 'solid' } },
    { selector: 'node.cited', style: { label: 'data(name)', 'border-width': 4, 'border-color': token('--accent'), 'border-style': 'solid' } },
    { selector: 'node.culled', style: { label: '' } },
    { selector: 'node.culled.hover, node.culled.picked, node.culled.cited', style: { label: 'data(name)' } },
    { selector: '.dim', style: { opacity: 0.15 } },
  ]
}
