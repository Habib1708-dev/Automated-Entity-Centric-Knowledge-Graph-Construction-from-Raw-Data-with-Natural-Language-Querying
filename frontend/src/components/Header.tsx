// The header (R223): the system's name, one tab per dataset, and what the active tab's graph holds and
// whether Neo4j has it loaded (questions go only to the loaded one). Holds no state of its own.
import { Database } from 'lucide-react'
import type { DatasetsView, SchemaView } from '../api/types'
import './Header.css'

interface Props {
  view: DatasetsView | null
  active: string | null
  schema: SchemaView | null
  onSelect: (id: string) => void
}

export function Header({ view, active, schema, onSelect }: Props) {
  const current = view?.datasets.find((d) => d.id === active)
  const things = schema?.things.reduce((sum, t) => sum + t.count, 0)
  return (
    <header className="header">
      <h1 className="header-name">Automated KG Builder With Natural Language Querying Engine</h1>
      <nav className="header-tabs" role="tablist" aria-label="Datasets">
        {view?.datasets.map((d) => (
          <button
            key={d.id}
            role="tab"
            aria-selected={d.id === active}
            className="header-tab"
            onClick={() => onSelect(d.id)}
          >
            {d.name}
            {d.loaded && <span className="header-dot" aria-label="loaded in Neo4j" />}
          </button>
        ))}
      </nav>
      <div className="header-status">
        {view && !view.neo4j && <span className="header-off">Neo4j is not reachable</span>}
        {current && view?.neo4j && (
          <span className={current.loaded ? 'header-on' : 'header-off'}>
            <Database size={16} aria-hidden />
            {current.loaded ? 'Loaded in Neo4j' : 'Not loaded'}
          </span>
        )}
        {schema && things !== undefined && (
          <span>
            {things.toLocaleString()} things
            <span className="header-extra"> · {schema.chunks.toLocaleString()} passages</span>
          </span>
        )}
      </div>
    </header>
  )
}
