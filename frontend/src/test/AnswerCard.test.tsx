// The answer as the chat shows it (R223), rendered from a recorded answer and its stream: the answer, its
// outcome and cost; every step once the steps are opened, the refused call and the send-back among them; and a
// source number that opens its source.
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnswerCard } from '../components/AnswerCard'
import type { Exchange } from '../lib/exchange'
import { ANSWER, EVENTS } from './fixtures'

const DONE: Exchange = {
  id: 1, question: 'When was the kettle planned?', variant: 'agent', started: 0, events: EVENTS, answer: ANSWER,
  seconds: 4.2, cost: 0.0187, error: null, running: false,
}

afterEach(cleanup)

describe('AnswerCard', () => {
  it('shows the answer with its outcome, time and cost, its steps folded', () => {
    render(<AnswerCard exchange={DONE} active={null} onOpenSource={() => undefined} />)
    expect(screen.getByText('The table gives 1903.')).toBeTruthy()
    expect(screen.getByText('Answer')).toBeTruthy()
    expect(screen.getByText('4.2 s')).toBeTruthy()
    expect(screen.getByText('$0.019')).toBeTruthy()
    expect(screen.queryByText('search_text')).toBeNull()
  })

  it('lists every step once opened, the refused call and the send-back included', () => {
    render(<AnswerCard exchange={DONE} active={null} onOpenSource={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: /Steps/ }))
    for (const tool of ['search_text', 'nope', 'query_tables']) expect(screen.getByText(tool)).toBeTruthy()
    expect(screen.getByText("Refused: no tool 'nope'")).toBeTruthy()
    expect(screen.getByText('Answer rested on one search, sent back')).toBeTruthy()
    expect(screen.getByText('Model wrote the answer')).toBeTruthy()
  })

  it('opens a source from its number', () => {
    const open = vi.fn()
    render(<AnswerCard exchange={DONE} active={null} onOpenSource={open} />)
    fireEvent.click(screen.getByRole('button', { name: /Query/ }))
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ n: 2, id: 'q-1', kind: 'query' }))
  })

  it('keeps the steps open and growing while the agent works', () => {
    const running = { ...DONE, answer: null, seconds: null, cost: null, running: true, events: EVENTS.slice(0, 2) }
    render(<AnswerCard exchange={running} active={null} onOpenSource={() => undefined} />)
    expect(screen.getByText('search_text')).toBeTruthy()
    expect(screen.getByText('Waiting for the model…')).toBeTruthy()
  })
})
