// One question and what came back (R223): the events as they streamed, then the answer, its seconds and
// cost, or the error that ended it. The chat keeps one list of these per dataset tab.
import type { LLMEvent, StreamEvent, SystemAnswer } from '../api/types'

export interface Exchange {
  id: number
  question: string
  variant: string
  started: number // performance.now() when asked, for the live clock
  events: StreamEvent[]
  answer: SystemAnswer | null
  seconds: number | null
  cost: number | null
  error: string | null
  running: boolean
}

/** `exchange` after one more event: the answer and the error end it; every event is kept in order. */
export function withEvent(exchange: Exchange, event: StreamEvent): Exchange {
  const next = { ...exchange, events: [...exchange.events, event] }
  if (event.event === 'answer') return { ...next, answer: event.answer, seconds: event.seconds, cost: event.cost_usd ?? null, running: false }
  if (event.event === 'error') return { ...next, error: event.message, running: false }
  return next
}

export const llmEvents = (exchange: Exchange): LLMEvent[] =>
  exchange.events.flatMap((e) => (e.event === 'llm' ? [e] : []))
