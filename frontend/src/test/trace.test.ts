// The page's pure logic (R223): steps rebuilt from the stream in order, each call with the model time that
// proposed it; the totals; the sources numbered in the order code kept them, with what the answer recorded of
// each; and the server-sent-event parser keeping an unfinished event for the next chunk.
import { describe, expect, it } from 'vitest'
import { parseEvents } from '../api/client'
import { sourcesOf, stepsFromAnswer, stepsFromEvents, totals } from '../lib/trace'
import { ANSWER, EVENTS } from './fixtures'

describe('steps', () => {
  it('pairs each call with the model time that proposed it, then the send-back and the answer', () => {
    const steps = stepsFromEvents(EVENTS)
    expect(steps.map((s) => s.kind)).toEqual(['call', 'call', 'call', 'reply', 'sent_back', 'reply'])
    expect(steps.slice(0, 3).map((s) => (s.kind === 'call' ? [s.call.tool, s.modelMs] : null))).toEqual([
      ['search_text', 900], ['nope', 700], ['query_tables', 800],
    ])
    expect(steps[4]).toEqual({ kind: 'sent_back', turn: 4, reason: 'recheck' })
  })

  it('counts run and refused calls, model and tool time, requests, cache hits and tokens', () => {
    const llm = EVENTS.flatMap((e) => (e.event === 'llm' ? [e] : []))
    expect(totals(stepsFromEvents(EVENTS), llm)).toEqual({
      calls: 2, refused: 1, modelMs: 3500, toolMs: 520, requests: 2, cached: 1, promptTokens: 100, outputTokens: 15,
    })
  })

  it('falls back to the trace, untimed, for an answer seen without its stream', () => {
    expect(stepsFromAnswer(ANSWER).map((s) => (s.kind === 'call' ? s.modelMs : 'x'))).toEqual([null, null, null])
  })
})

describe('sources', () => {
  it('numbers the kept citations in order, each with its passage, quote, query and check', () => {
    const [passage, query] = sourcesOf(ANSWER)
    expect([passage.n, passage.kind, passage.quote, passage.chunk?.context]).toEqual([1, 'chunk', 'a new kettle', 'Kettle review'])
    expect([query.n, query.kind, query.query?.rows, query.check?.number_found]).toEqual([2, 'query', [['1903']], true])
  })
})

describe('the event stream', () => {
  it('returns complete events and keeps the unfinished tail', () => {
    const { events, rest } = parseEvents('data: {"event":"model","turn":1,"ms":5}\n\ndata: {"event":"mo')
    expect(events).toEqual([{ event: 'model', turn: 1, ms: 5 }])
    expect(rest).toBe('data: {"event":"mo')
  })
})
