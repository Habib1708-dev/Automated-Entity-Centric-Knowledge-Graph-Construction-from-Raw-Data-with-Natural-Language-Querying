// The graph region (R223): one dataset's graph drawn with Cytoscape, from the build read offline, so every
// tab can be browsed whichever graph Neo4j holds. It opens on the busiest things (the overview); search or a
// click narrows it to one thing's neighbourhood, and a clicked thing opens its detail panel. Things an answer
// cites carry the accent ring; while a source is open (`focus`), everything else dims.
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { Search, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { SchemaView, Subgraph, Thing } from '../api/types'
import { graphStyle } from '../lib/graphStyle'
import { Legend } from './Legend'
import { ThingPanel } from './ThingPanel'
import './GraphPanel.css'

// The overview's size: past ~80 things the labels of a laptop-sized canvas overlap; a neighbourhood is
// smaller so the focused thing stays readable.
const OVERVIEW = 80
const NEIGHBOURHOOD = 40
const LABELLED = 10 // only the busiest things carry a label until hovered or selected
const LABEL_PX = 15 // a label's size on screen, whatever the zoom (the page's smallest text size)

interface Props {
  dataset: string
  schema: SchemaView | null
  cited: string[]
  focus: string[] | null // the open source's things; null while no source is open
}

// The width the thing panel covers at the canvas's right edge (index.css --panel-w): a focused thing is
// centred in the part of the canvas left visible
const PANEL_PX = 420

// a canvas this narrow (a phone) has no room for standing labels: names show on hover and selection only
const NARROW_PX = 600

function elements(graph: Subgraph, keep: string | null, labels: number): ElementDefinition[] {
  // a thing linked to nothing else drawn adds a dot and no information: the overview leaves it out
  const linked = new Set(graph.links.flatMap((l) => [l.source, l.target]))
  const things = graph.things.filter((t) => linked.has(t.id) || t.id === keep || graph.things.length === 1)
  const labelled = new Set(things.slice(0, labels).map((t) => t.id))
  const nodes = things.map((t) => ({
    data: { ...t, size: Math.min(52, 16 + 7 * Math.log2(1 + t.degree)) /* smaller shapes leave labels room */, label: labelled.has(t.id) ? t.name : '' },
  }))
  const edges = graph.links.map((l) => ({
    data: { id: `${l.source}|${l.type}|${l.target}`, source: l.source, target: l.target, type: l.type, origin: l.origin, count: l.count },
  }))
  return [...nodes, ...edges]
}

export function GraphPanel({ dataset, schema, cited, focus }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const [graph, setGraph] = useState<Subgraph | null>(null)
  const [centre, setCentre] = useState<Thing | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Thing[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // maxZoom: past 1.6 a focused thing's node fills the canvas and its neighbours leave the view
    const instance = cytoscape({ container: container.current, style: graphStyle(), minZoom: 0.2, maxZoom: 1.6 })
    instance.on('tap', 'node', (e) => setSelected(e.target.id()))
    instance.on('mouseover', 'node', (e) => e.target.addClass('hover'))
    instance.on('mouseout', 'node', (e) => e.target.removeClass('hover'))
    // Cytoscape scales labels with the drawing, so a fitted overview would shrink them below the page's
    // smallest text; they are resized against the zoom instead, keeping LABEL_PX on screen
    const scaleLabels = () => {
      const zoom = instance.zoom()
      instance
        .style()
        .selector('node')
        .style({ 'font-size': LABEL_PX / zoom, 'text-margin-y': 6 / zoom, 'text-max-width': `${180 / zoom}px` })
        .update()
    }
    // a label that would overlap a busier thing's label, or another thing's shape, is hidden until hovered
    // (Bloom's collision culling): a label half under a node is worse than none
    const cullLabels = () => {
      const kept: cytoscape.BoundingBox12[] = []
      const overlaps = (a: cytoscape.BoundingBox12, b: cytoscape.BoundingBox12) =>
        a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2
      const bodies = instance.nodes().map((n) => ({ id: n.id(), box: n.boundingBox({ includeLabels: false }) }))
      const labelled = instance.nodes('[label != ""]').removeClass('culled')
      labelled
        .sort((a, b) => b.data('degree') - a.data('degree'))
        .forEach((n) => {
          const box = n.boundingBox({ includeNodes: false, includeLabels: true })
          // a shape grazing a label's edge leaves it readable; one covering a quarter of it does not
          const covered = (b: cytoscape.BoundingBox12) =>
            (Math.max(0, Math.min(b.x2, box.x2) - Math.max(b.x1, box.x1)) *
              Math.max(0, Math.min(b.y2, box.y2) - Math.max(b.y1, box.y1))) /
              ((box.x2 - box.x1) * (box.y2 - box.y1)) >
            0.25
          const hit = kept.some((k) => overlaps(k, box)) || bodies.some((b) => b.id !== n.id() && covered(b.box))
          if (hit) n.addClass('culled')
          else kept.push(box)
        })
    }
    let settle: ReturnType<typeof setTimeout> | undefined
    instance.on('zoom', () => {
      scaleLabels()
      clearTimeout(settle)
      settle = setTimeout(cullLabels, 120)
    })
    instance.on('layoutstop', () => {
      // the legend floats over the canvas's bottom: keep the drawing clear of it
      instance.fit(instance.elements(), 56)
      instance.panBy({ x: 0, y: -28 })
      scaleLabels()
      cullLabels()
    })
    const scheme = window.matchMedia('(prefers-color-scheme: dark)')
    const restyle = () => {
      instance.style(graphStyle())
      scaleLabels()
    }
    scheme.addEventListener('change', restyle)
    cy.current = instance
    return () => {
      scheme.removeEventListener('change', restyle)
      instance.destroy()
    }
  }, [])

  // the panel opens on the overview; App remounts it per tab (its `key`), so a new tab starts there too
  useEffect(() => {
    api.overview(dataset, OVERVIEW).then(setGraph, (e: Error) => setError(e.message))
  }, [dataset])

  useEffect(() => {
    const instance = cy.current
    if (!instance || !graph) return
    instance.elements().remove()
    instance.add(elements(graph, centre?.id ?? null, instance.width() < NARROW_PX ? 0 : LABELLED))
    // strong repulsion and long edges: the default cose packing overlaps the labels of a dense overview
    instance
      .layout({ name: 'cose', animate: false, padding: 56, nodeRepulsion: () => 40000, idealEdgeLength: () => 110, nodeOverlap: 24, componentSpacing: 120 })
      .run()
  }, [graph, centre])

  // the selected thing carries the accent ring (a class, since re-added elements lose Cytoscape's own
  // selection) and is centred in the canvas left visible beside its detail panel
  useEffect(() => {
    const instance = cy.current
    if (!instance) return
    instance.nodes().removeClass('picked')
    if (!selected) return
    const node = instance.getElementById(selected)
    if (node.empty()) return
    node.addClass('picked')
    const visible = instance.width() - PANEL_PX
    const at = node.renderedPosition()
    instance.animate({ panBy: { x: visible / 2 - at.x, y: instance.height() / 2 - at.y }, duration: 250, easing: 'ease-out-cubic' })
  }, [selected, graph])

  // the answer's cited things, and the dimming while one source is open
  useEffect(() => {
    const instance = cy.current
    if (!instance) return
    instance.nodes().removeClass('cited dim')
    instance.edges().removeClass('dim')
    instance.nodes().filter((n) => cited.includes(n.id())).addClass('cited')
    if (!focus?.length) return
    const nodes = instance.nodes().filter((n) => focus.includes(n.id()))
    if (nodes.empty()) {
      // the source's things are outside the drawn part: draw the first one's neighbourhood instead
      api.neighbourhood(dataset, focus[0], NEIGHBOURHOOD).then((g) => {
        setCentre(g.things.find((t) => t.id === focus[0]) ?? null)
        setGraph(g)
      }, () => undefined)
      return
    }
    instance.elements().not(nodes.union(nodes.edgesWith(nodes))).addClass('dim')
    // one or two things are centred at the current zoom; more are fitted together
    const view = nodes.length < 3 ? { center: { eles: nodes } } : { fit: { eles: nodes, padding: 120 } }
    instance.animate({ ...view, duration: 300, easing: 'ease-out-cubic' })
  }, [cited, focus, graph, dataset])

  useEffect(() => {
    const text = query.trim()
    if (!text) return // the list is hidden while the box is empty (see `shown` below)
    const timer = setTimeout(() => api.search(dataset, text).then(setHits, () => setHits([])), 200)
    return () => clearTimeout(timer)
  }, [query, dataset])

  const showNeighbourhood = (thing: Thing) => {
    setCentre(thing)
    setSelected(thing.id)
    setQuery('')
    api.neighbourhood(dataset, thing.id, NEIGHBOURHOOD).then(setGraph, (e: Error) => setError(e.message))
  }

  const showOverview = () => {
    setCentre(null)
    api.overview(dataset, OVERVIEW).then(setGraph, (e: Error) => setError(e.message))
  }

  return (
    <section className={`graph ${selected ? 'graph-panel-open' : ''}`} aria-label="Graph">
      <div className="graph-bar">
        <h2 className="graph-title">Graph</h2>
        <div className="graph-view">
          {centre ? (
            <>
              <span>
                Around <strong>{centre.name}</strong>
              </span>
              <button className="graph-link" onClick={showOverview}>
                Back to overview
              </button>
            </>
          ) : (
            graph && (
              <span>
                The {graph.things.length} busiest of {graph.total.toLocaleString()} things
              </span>
            )
          )}
        </div>
        <div className="graph-search">
          <Search size={18} aria-hidden className="graph-search-icon" />
          <input
            type="search"
            placeholder="Find a thing"
            aria-label="Find a thing in the graph"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="graph-search-clear" aria-label="Clear search" onClick={() => setQuery('')}>
              <X size={16} />
            </button>
          )}
          {query.trim() && hits.length > 0 && (
            <ul className="graph-hits" role="listbox">
              {hits.map((t) => (
                <li key={t.id}>
                  <button onClick={() => showNeighbourhood(t)}>
                    <span>{t.name}</span>
                    <span className="graph-hit-type">{t.type}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="graph-canvas" ref={container} />
      {error && <p className="graph-error">The graph could not be read: {error}</p>}
      <Legend schema={schema} />
      {selected && (
        <ThingPanel
          key={selected}
          dataset={dataset}
          id={selected}
          onClose={() => setSelected(null)}
          onOpen={(id) => setSelected(id)}
          onNeighbourhood={showNeighbourhood}
          drawn={centre?.id === selected}
        />
      )}
    </section>
  )
}
