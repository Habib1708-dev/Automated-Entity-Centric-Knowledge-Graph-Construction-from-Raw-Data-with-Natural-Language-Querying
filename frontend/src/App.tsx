// The page (R223): the header with the dataset tabs over two work areas, the graph and the chat. App owns
// what crosses them: the active tab (remembered in this browser), each tab's exchanges, the open source, and
// the things the latest answer cites, which the graph rings. It asks one question at a time, as the API does.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, api, ask } from './api/client'
import type { DatasetsView, SchemaView, StreamEvent } from './api/types'
import { ChatPanel } from './components/ChatPanel'
import { GraphPanel } from './components/GraphPanel'
import { Header } from './components/Header'
import { type Exchange, withEvent } from './lib/exchange'
import { sourceRef, sourcesOf, type Source } from './lib/trace'
import './App.css'

const TAB_KEY = 'kg-builder.tab'

function rememberedTab(): string | null {
  try {
    return localStorage.getItem(TAB_KEY)
  } catch {
    return null // storage blocked (a private window): start on the default tab
  }
}

function remember(tab: string) {
  try {
    localStorage.setItem(TAB_KEY, tab)
  } catch {
    // storage blocked: the tab is simply not remembered
  }
}

let nextId = 1

export default function App() {
  const [view, setView] = useState<DatasetsView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [schemas, setSchemas] = useState<Record<string, SchemaView>>({})
  const [chats, setChats] = useState<Record<string, Exchange[]>>({})
  const [source, setSource] = useState<Source | null>(null)
  const [busy, setBusy] = useState(false)
  // where each source of the latest answer sits on the graph: source id -> thing ids (POST /locate)
  const [placed, setPlaced] = useState<Record<string, string[]>>({})

  useEffect(() => {
    api.datasets().then(
      (v) => {
        setView(v)
        const saved = rememberedTab()
        const ids = v.datasets.map((d) => d.id)
        setActive(saved && ids.includes(saved) ? saved : (v.loaded ?? ids[0] ?? null))
      },
      (e: Error) => setError(e.message),
    )
  }, [])

  useEffect(() => {
    if (!active) return
    // without a schema the header and legend show no counts; nothing else depends on it
    api.schema(active).then((s) => setSchemas((all) => ({ ...all, [active]: s })), () => undefined)
  }, [active])

  const select = (id: string) => {
    setActive(id)
    setSource(null) // a source belongs to an answer of the tab it was opened on
    remember(id)
  }
  const schema = active ? (schemas[active] ?? null) : null

  const update = useCallback((dataset: string, id: number, change: (x: Exchange) => Exchange) => {
    setChats((all) => ({ ...all, [dataset]: (all[dataset] ?? []).map((x) => (x.id === id ? change(x) : x)) }))
  }, [])

  const onAsk = async (question: string, variant: string) => {
    if (!active) return
    const dataset = active
    const id = nextId++
    const exchange: Exchange = {
      id, question, variant, started: performance.now(), events: [], answer: null, seconds: null, cost: null,
      error: null, running: true,
    }
    setChats((all) => ({ ...all, [dataset]: [...(all[dataset] ?? []), exchange] }))
    setSource(null)
    setBusy(true)
    try {
      await ask(dataset, question, variant, (event: StreamEvent) => update(dataset, id, (x) => withEvent(x, event)))
    } catch (e) {
      const message = e instanceof ApiError || e instanceof Error ? e.message : String(e)
      update(dataset, id, (x) => ({ ...x, error: message, running: false }))
    } finally {
      // a stream cut off without its end event still ends the exchange
      update(dataset, id, (x) => (x.running ? { ...x, running: false, error: x.error ?? 'The answer stream ended early.' } : x))
      setBusy(false)
    }
  }

  const exchanges = useMemo(() => (active ? (chats[active] ?? []) : []), [active, chats])
  const latest = [...exchanges].reverse().find((x) => x.answer)
  useEffect(() => {
    if (!active || !latest?.answer) return
    const refs = sourcesOf(latest.answer).map(sourceRef)
    // without a placement the graph simply rings nothing; the answer and its sources still show
    api.locate(active, refs).then(setPlaced, () => undefined)
  }, [active, latest])
  const cited = useMemo(() => [...new Set(Object.values(placed).flat())], [placed])
  const focus = source ? (placed[source.id] ?? []) : null
  const dataset = view?.datasets.find((d) => d.id === active)

  return (
    <div className="app">
      <Header view={view} active={active} schema={schema} onSelect={select} />
      {error && (
        <p className="app-error">
          The interface cannot reach its API ({error}). Start it with <code>uv run kg serve</code>.
        </p>
      )}
      {view && dataset && (
        <main className="app-main">
          <GraphPanel key={dataset.id} dataset={dataset.id} schema={schema} cited={cited} focus={focus} />
          <ChatPanel
            dataset={dataset}
            view={view}
            exchanges={exchanges}
            busy={busy}
            source={source}
            onAsk={onAsk}
            onOpenSource={setSource}
          />
        </main>
      )}
    </div>
  )
}
