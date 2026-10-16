// The chat region (R223): the exchanges asked of the active dataset, newest last, and the question box with
// the agent variant. Questions go only to the dataset whose graph Neo4j holds; on any other tab the box says
// which one is loaded instead of failing after the question is sent.
import { ArrowUp } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { DatasetInfo, DatasetsView } from '../api/types'
import type { Exchange } from '../lib/exchange'
import type { Source } from '../lib/trace'
import { AnswerCard } from './AnswerCard'
import { SourceDrawer } from './SourceDrawer'
import './ChatPanel.css'

interface Props {
  dataset: DatasetInfo
  view: DatasetsView
  exchanges: Exchange[]
  busy: boolean
  source: Source | null
  onAsk: (question: string, variant: string) => void
  onOpenSource: (source: Source | null) => void
}

function closedReason(dataset: DatasetInfo, view: DatasetsView): string | null {
  if (!view.neo4j) return 'Neo4j is not reachable, so questions are off. Start it with docker compose up -d.'
  if (dataset.loaded) return null
  const loaded = view.datasets.find((d) => d.id === view.loaded)
  return loaded
    ? `Questions go only to the graph loaded in Neo4j, which is ${loaded.name}. You can still browse this graph.`
    : `Neo4j holds none of these datasets, so questions are off. You can still browse this graph.`
}

export function ChatPanel({ dataset, view, exchanges, busy, source, onAsk, onOpenSource }: Props) {
  const [question, setQuestion] = useState('')
  const [variant, setVariant] = useState(view.variants[0] ?? 'agent')
  const list = useRef<HTMLDivElement>(null)
  const closed = closedReason(dataset, view)
  const last = exchanges[exchanges.length - 1]

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: 'smooth' })
  }, [exchanges.length, last?.events.length, last?.running])

  const send = () => {
    const text = question.trim()
    if (!text || busy || closed) return
    onAsk(text, variant)
    setQuestion('')
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <section className="chat" aria-label="Questions">
      <div className="chat-list" ref={list}>
        {exchanges.length === 0 ? (
          <div className="chat-empty">
            <h2>Ask {dataset.name}</h2>
            <p>
              Ask a question in plain words. Each answer shows every step the agent took, how long each one took, what
              it cost, and the sources it rests on: open a number to see the passage, the table row or the query.
            </p>
          </div>
        ) : (
          exchanges.map((x) => <AnswerCard key={x.id} exchange={x} active={source} onOpenSource={onOpenSource} />)
        )}
      </div>
      <div className="chat-box">
        {closed && <p className="chat-closed">{closed}</p>}
        <div className={`chat-input ${closed ? 'chat-input-off' : ''}`}>
          <textarea
            rows={2}
            placeholder={closed ? 'Questions are off for this dataset' : `Ask about ${dataset.name}`}
            aria-label="Your question"
            value={question}
            disabled={closed !== null}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKey}
          />
          <div className="chat-actions">
            <label className="chat-variant">
              <span>Agent</span>
              <select value={variant} onChange={(e) => setVariant(e.target.value)} disabled={closed !== null}>
                {view.variants.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <button className="chat-send" onClick={send} disabled={!question.trim() || busy || closed !== null}>
              <ArrowUp size={18} aria-hidden />
              {busy ? 'Answering' : 'Ask'}
            </button>
          </div>
        </div>
      </div>
      {source && <SourceDrawer key={source.id} dataset={dataset.id} source={source} onClose={() => onOpenSource(null)} />}
    </section>
  )
}
