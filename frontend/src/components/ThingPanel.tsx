// One thing's detail (R223), opened by clicking it in the graph: its kind and type, its properties with the
// table row each came from, and its facts, each with its provenance (a table row, or the sentence and passage
// of a text claim). A fact's other end opens that thing in turn.
import { Network, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Fact, Thing, ThingDetail } from '../api/types'
import './ThingPanel.css'

interface Props {
  dataset: string
  id: string
  onClose: () => void
  onOpen: (id: string) => void
  onNeighbourhood: (thing: Thing) => void
  drawn: boolean // its neighbourhood is the one drawn: the button would do nothing
}

const KIND_NAME: Record<Thing['kind'], string> = {
  record: 'Table record',
  individual: 'Named in the text',
  concept: 'Concept',
  mention: 'Unresolved mention',
}

function FactItem({ fact, self, ends, onOpen }: { fact: Fact; self: string; ends: Map<string, Thing>; onOpen: (id: string) => void }) {
  const outgoing = fact.source === self
  const other = ends.get(outgoing ? fact.target : fact.source)
  return (
    <li className="fact">
      <div className="fact-line">
        <span className="fact-type">{outgoing ? fact.type : `${fact.type} (of)`}</span>
        {other ? (
          <button className="fact-other" onClick={() => onOpen(other.id)}>
            {other.name}
          </button>
        ) : (
          <span>itself</span>
        )}
      </div>
      {fact.origin === 'text' ? (
        <>
          <blockquote className="fact-evidence">{fact.evidence}</blockquote>
          <code className="fact-source">passage {fact.chunk_id}</code>
        </>
      ) : (
        <code className="fact-source">{fact.row || 'table relationship'}</code>
      )}
    </li>
  )
}

export function ThingPanel({ dataset, id, onClose, onOpen, onNeighbourhood, drawn }: Props) {
  const [detail, setDetail] = useState<ThingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // the parent remounts the panel per thing (its `key`), so the state starts empty for each
  useEffect(() => {
    api.thing(dataset, id).then(setDetail, (e: Error) => setError(e.message))
  }, [dataset, id])

  const ends = new Map(detail?.neighbours.map((t) => [t.id, t]))
  return (
    <aside className="thing" aria-label="Thing detail">
      <div className="thing-head">
        <div>
          <h3 className="thing-name">{detail?.thing.name ?? id}</h3>
          {detail && (
            <p className="thing-kind">
              {KIND_NAME[detail.thing.kind]} · {detail.thing.type}
            </p>
          )}
        </div>
        <button className="thing-close" aria-label="Close" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {error && <p className="thing-error">{error}</p>}
      {detail && (
        <div className="thing-body">
          {!drawn && (
            <button className="thing-action" onClick={() => onNeighbourhood(detail.thing)}>
              <Network size={18} aria-hidden /> Show its neighbourhood
            </button>
          )}
          {detail.properties.length > 0 && (
            <section>
              <h4 className="thing-section">Properties</h4>
              <dl className="thing-props">
                {detail.properties.map((p) => (
                  <div key={p.name}>
                    <dt>{p.name}</dt>
                    <dd>{p.value}</dd>
                  </div>
                ))}
              </dl>
              <code className="fact-source">from {detail.properties[0].source}</code>
            </section>
          )}
          <section>
            <h4 className="thing-section">
              Facts <span className="thing-count">{detail.total_facts}</span>
            </h4>
            <ul className="facts">
              {detail.facts.map((f, i) => (
                <FactItem key={i} fact={f} self={detail.thing.id} ends={ends} onOpen={onOpen} />
              ))}
            </ul>
            {detail.total_facts > detail.facts.length && (
              <p className="thing-more">
                Showing {detail.facts.length} of {detail.total_facts}.
              </p>
            )}
          </section>
        </div>
      )}
    </aside>
  )
}
