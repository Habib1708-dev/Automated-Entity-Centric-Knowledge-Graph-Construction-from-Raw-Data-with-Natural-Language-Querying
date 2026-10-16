// A small answer and its stream for the page's tests (R223), shaped like a real `agent` answer (one passage
// and one table query cited, one call refused, one send-back); the values are made up for the tests.
import type { StreamEvent, SystemAnswer } from '../api/types'

const call = (turn: number, tool: string, returned: string[], latency: number, refused = '') => ({
  turn, tool, arguments: '{"text": "kettle"}', refused, note: '', returned, latency_ms: latency,
})

export const ANSWER = {
  question_id: 'ask', system: 'agent', retrieved: ['d.md#0'], entities: null, number: 1903, text: 'The table gives 1903.',
  citations: [{ chunk_id: 'd.md#0', quote: 'a new kettle' }],
  outcome: 'answer', latency_s: null, route: null, trace: null, exact: null, plan: null, screen: null,
  shown: [{ chunk_id: 'd.md#0', context: 'Kettle review', text: 'They planned a new kettle in 1903.', excerpt: '' }],
  agent: {
    calls: [call(1, 'search_text', ['d.md#0'], 400), call(2, 'nope', [], 0, "no tool 'nope'"), call(3, 'query_tables', ['q-1'], 120)],
    turns: 4, stopped: 'answer', prompt_chars: 9000, cited: ['d.md#0', 'q-1'], dropped: [],
    citation_checks: [
      { id: 'd.md#0', kind: 'chunk', quote_found: true, entities_found: [], number_found: true },
      { id: 'q-1', kind: 'query', quote_found: null, entities_found: [], number_found: true },
    ],
    queries: [{ query_id: 'q-1', tool: 'query_tables', statement: 'SELECT inception FROM kettles', parameters: '{}',
                sources: ['kettles'], files: ['staging/kettles.csv'], columns: ['inception'], rows: [['1903']], refs: [], total: 1 }],
    readings: [], outcome: 'answer', llm_ms: 3000, wall_ms: 4000,
  },
} as unknown as SystemAnswer // the trace's many recorded-only fields are left at their absence here

export const EVENTS: StreamEvent[] = [
  { event: 'model', turn: 1, ms: 900 },
  { event: 'call', call: ANSWER.agent!.calls[0], result: '{"chunks": []}', cut: false },
  { event: 'model', turn: 2, ms: 700 },
  { event: 'call', call: ANSWER.agent!.calls[1], result: '', cut: false },
  { event: 'model', turn: 3, ms: 800 },
  { event: 'call', call: ANSWER.agent!.calls[2], result: '{"rows": [["1903"]]}', cut: false },
  { event: 'model', turn: 4, ms: 600 },
  { event: 'sent_back', turn: 4, reason: 'recheck' },
  { event: 'model', turn: 4, ms: 500 },
  { event: 'llm', model: 'm', kind: 'generate', ok: true, cache_hit: false, prompt_tokens: 100, completion_tokens: 10, thinking_tokens: 5, latency_s: 1, cost_usd: 0.001 },
  { event: 'llm', model: 'm', kind: 'generate', ok: true, cache_hit: true, prompt_tokens: null, completion_tokens: null, thinking_tokens: null, latency_s: 0, cost_usd: 0 },
]
