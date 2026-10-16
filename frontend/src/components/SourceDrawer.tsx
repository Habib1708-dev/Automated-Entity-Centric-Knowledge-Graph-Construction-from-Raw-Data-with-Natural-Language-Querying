// One source of an answer, opened from its number (R223): what code checked against it (quote found, names
// found, number found; recorded, never enforced, R210), and its content. A passage shows its whole text with
// the quoted words marked; a query its statement, parameters, files and every row it returned; a thing its
// properties and their rows. While the drawer is open the graph dims all but the cited thing.
import { Check, Minus, X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { CitationCheck, ThingDetail } from '../api/types'
import type { Source } from '../lib/trace'
import './SourceDrawer.css'

const TITLE: Record<Source['kind'], string> = { chunk: 'Passage', claim: 'Claim', node: 'Thing', query: 'Query' }

// passages keep their source's markdown; its heading marks are structure, not words the source says
const plain = (text: string) => text.replace(/^#{1,6}\s+/gm, '')

function Marked({ text: raw, quote }: { text: string; quote: string }) {
  const text = plain(raw)
  const at = quote ? text.indexOf(quote) : -1
  if (at < 0) return <p className="drawer-text">{text}</p>
  return (
    <p className="drawer-text">
      {text.slice(0, at)}
      <mark>{quote}</mark>
      {text.slice(at + quote.length)}
    </p>
  )
}

function Mark({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  if (ok === null) return null
  return (
    <li className={ok ? 'check-ok' : 'check-no'}>
      {ok ? <Check size={16} aria-hidden /> : <Minus size={16} aria-hidden />}
      {children}
    </li>
  )
}

function Checks({ check }: { check: CitationCheck | null }) {
  if (!check) return null
  return (
    <ul className="drawer-checks" aria-label="Code's checks">
      <Mark ok={check.quote_found}>{check.quote_found ? 'Quote found in the passage' : 'Quote not found in the passage'}</Mark>
      <Mark ok={check.number_found}>{check.number_found ? 'Number found here' : 'Number not found here'}</Mark>
      {check.entities_found.length > 0 && (
        <Mark ok={true}>Names found here: {check.entities_found.join(', ')}</Mark>
      )}
    </ul>
  )
}

interface Props {
  dataset: string
  source: Source
  onClose: () => void
}

export function SourceDrawer({ dataset, source, onClose }: Props) {
  const [text, setText] = useState<string | null>(source.chunk?.text ?? null)
  const [thing, setThing] = useState<ThingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // the chat remounts the drawer per source (its `key`), so the state starts from this source alone
  useEffect(() => {
    if (source.kind === 'chunk' && !source.chunk) {
      api.chunk(dataset, source.id).then((c) => setText(c.text), (e: Error) => setError(e.message))
    }
    if (source.kind === 'node') api.thing(dataset, source.id).then(setThing, (e: Error) => setError(e.message))
  }, [dataset, source])

  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [onClose])

  const query = source.query
  return (
    <aside className="drawer" aria-label={`Source ${source.n}`}>
      <div className="drawer-head">
        <h3>
          <span className="drawer-n">{source.n}</span> {TITLE[source.kind]}
        </h3>
        <button className="drawer-close" aria-label="Close source" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="drawer-body">
        <code className="drawer-id">{source.id}</code>
        <Checks check={source.check} />
        {error && <p className="drawer-error">{error}</p>}
        {source.kind === 'chunk' && (
          <>
            {source.chunk?.context && <p className="drawer-context">{source.chunk.context}</p>}
            {text !== null && <Marked text={text} quote={source.quote} />}
            {source.chunk?.excerpt && (
              <p className="drawer-note">The agent read this passage as an excerpt; the whole passage is shown.</p>
            )}
          </>
        )}
        {source.kind === 'claim' && (
          <p className="drawer-note">A claim extracted from the text; its passage is listed among the sources.</p>
        )}
        {source.kind === 'node' && thing && (
          <>
            <p className="drawer-context">
              {thing.thing.name} · {thing.thing.type}
            </p>
            <dl className="drawer-props">
              {thing.properties.map((p) => (
                <div key={p.name}>
                  <dt>{p.name}</dt>
                  <dd>{p.value}</dd>
                </div>
              ))}
            </dl>
            {thing.properties[0] && <code className="drawer-row">from {thing.properties[0].source}</code>}
          </>
        )}
        {query && (
          <>
            <p className="drawer-label">Statement ({query.tool})</p>
            <pre>{query.statement}</pre>
            {query.parameters !== '{}' && (
              <>
                <p className="drawer-label">Parameters</p>
                <pre>{query.parameters}</pre>
              </>
            )}
            <p className="drawer-label">Read from {query.files.join(', ') || query.sources.join(', ')}</p>
            <div className="drawer-table">
              <table>
                <thead>
                  <tr>
                    {query.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {query.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((v, j) => (
                        <td key={j}>{v}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="drawer-note">
              {query.total.toLocaleString()} {query.total === 1 ? 'row' : 'rows'} in all.
            </p>
          </>
        )}
      </div>
    </aside>
  )
}
