// What an answer shows of how it was made (R223): the agent's steps, rebuilt from the live events or, for an
// answer without them, from its trace; the run's totals; and its numbered sources. Pure functions, so the
// tests check them without a browser.
import type {
  AgentCall,
  CitationCheck,
  LLMEvent,
  Outcome,
  QueryRecord,
  SentBackEvent,
  ShownChunk,
  SourceRef,
  StreamEvent,
  SystemAnswer,
} from '../api/types'

export type SendBackReason = SentBackEvent['reason']

export type StepRow =
  | { kind: 'call'; turn: number; modelMs: number | null; call: AgentCall; result: string; cut: boolean }
  | { kind: 'reply'; turn: number; modelMs: number }
  | { kind: 'sent_back'; turn: number; reason: SendBackReason }

/** The steps in the order they happened: each tool call with the model time that proposed it, each model
 * reply that proposed no call (an answer, or one sent back), and each send-back. */
export function stepsFromEvents(events: StreamEvent[]): StepRow[] {
  const rows: StepRow[] = []
  let pending: { turn: number; ms: number } | null = null
  const flush = () => {
    if (pending) rows.push({ kind: 'reply', turn: pending.turn, modelMs: pending.ms })
    pending = null
  }
  for (const event of events) {
    if (event.event === 'model') {
      flush()
      pending = { turn: event.turn, ms: event.ms }
    } else if (event.event === 'call') {
      const ms: number | null = pending && pending.turn === event.call.turn ? pending.ms : null
      pending = null
      rows.push({ kind: 'call', turn: event.call.turn, modelMs: ms, call: event.call, result: event.result, cut: event.cut })
    } else if (event.event === 'sent_back') {
      flush()
      rows.push({ kind: 'sent_back', turn: event.turn, reason: event.reason })
    }
  }
  flush()
  return rows
}

/** The steps of an answer seen without its events (a refusal has none): its trace's calls, untimed models. */
export function stepsFromAnswer(answer: SystemAnswer): StepRow[] {
  return (answer.agent?.calls ?? []).map((call) => ({ kind: 'call', turn: call.turn, modelMs: null, call, result: '', cut: false }))
}

export interface Totals {
  calls: number
  refused: number
  modelMs: number
  toolMs: number
  requests: number
  cached: number
  promptTokens: number
  outputTokens: number
}

/** The run's totals from its steps and its LLM requests (output tokens include hidden thinking). */
export function totals(steps: StepRow[], llm: LLMEvent[]): Totals {
  const calls = steps.flatMap((s) => (s.kind === 'call' ? [s] : []))
  return {
    calls: calls.filter((c) => !c.call.refused).length,
    refused: calls.filter((c) => c.call.refused).length,
    modelMs: steps.reduce((sum, s) => sum + (s.kind !== 'sent_back' ? (s.modelMs ?? 0) : 0), 0),
    toolMs: calls.reduce((sum, c) => sum + c.call.latency_ms, 0),
    requests: llm.filter((e) => e.kind === 'generate').length,
    cached: llm.filter((e) => e.cache_hit).length,
    promptTokens: llm.reduce((sum, e) => sum + (e.prompt_tokens ?? 0), 0),
    outputTokens: llm.reduce((sum, e) => sum + (e.completion_tokens ?? 0) + (e.thinking_tokens ?? 0), 0),
  }
}

export type SourceKind = CitationCheck['kind']

export interface Source {
  n: number
  id: string
  kind: SourceKind
  check: CitationCheck | null
  quote: string
  chunk: ShownChunk | null
  query: QueryRecord | null
}

function kindOf(id: string, check: CitationCheck | undefined, chunk: ShownChunk | undefined): SourceKind {
  if (check) return check.kind
  if (chunk) return 'chunk'
  return id.startsWith('q-') ? 'query' : 'node'
}

/** The answer's sources, numbered in the order code kept them: the agent's kept citations, or for any other
 * system its cited chunks. Each carries what the answer itself records of it (the passage read, the query
 * run, code's check against it), so the drawer can show it without asking the API. */
export function sourcesOf(answer: SystemAnswer): Source[] {
  const ids = answer.agent ? answer.agent.cited : answer.citations.map((c) => c.chunk_id)
  const checks = new Map((answer.agent?.citation_checks ?? []).map((c) => [c.id, c]))
  const chunks = new Map(answer.shown.map((c) => [c.chunk_id, c]))
  const queries = new Map((answer.agent?.queries ?? []).map((q) => [q.query_id, q]))
  const quotes = new Map(answer.citations.map((c) => [c.chunk_id, c.quote]))
  return ids.map((id, i) => ({
    n: i + 1,
    id,
    kind: kindOf(id, checks.get(id), chunks.get(id)),
    check: checks.get(id) ?? null,
    quote: quotes.get(id) ?? '',
    chunk: chunks.get(id) ?? null,
    query: queries.get(id) ?? null,
  }))
}

/** What the API needs to place a source on the graph (POST /locate). */
export function sourceRef(source: Source): SourceRef {
  const q = source.query
  return { kind: source.kind, id: source.id, sources: q?.sources ?? [], columns: q?.columns ?? [], rows: q?.rows ?? [], refs: q?.refs ?? [] }
}

export const OUTCOME_LABEL: Record<Outcome, string> = {
  answer: 'Answer',
  clarify: 'Needs clarification',
  out_of_scope: 'Out of scope',
  insufficient_evidence: 'Not in the data',
  refuse: 'Refused',
}

export const SEND_BACK_LABEL: Record<SendBackReason, string> = {
  empty: 'Empty answer, sent back to the model',
  outcome: 'Outcome not borne out by the tools, sent back',
  form: 'Answer missing its declared form, sent back',
  recheck: 'Answer rested on one search, sent back',
  evidence: 'Passages it read were left out, sent back',
  citations: 'Cited ids no tool returned, sent back',
}

/** The answer's words: its text, else its named things, else its number. */
export function answerText(answer: SystemAnswer): string {
  if (answer.text) return answer.text
  if (answer.entities?.length) return answer.entities.join(', ')
  if (answer.number !== null && answer.number !== undefined) return String(answer.number)
  return 'No answer was given.'
}

export const seconds = (ms: number) => `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
export const dollars = (usd: number | null | undefined) =>
  usd === null || usd === undefined ? 'cost unknown' : usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`
