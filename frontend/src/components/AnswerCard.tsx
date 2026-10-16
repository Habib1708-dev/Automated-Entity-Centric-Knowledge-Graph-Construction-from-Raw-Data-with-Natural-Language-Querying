// One question and its answer (R223): the question, the answer's words, its outcome, time, cost and agent
// variant, its numbered sources (each opens the source drawer), and its steps. While the agent works the
// steps are open and grow live with a running clock; once it answers they fold away, one click from view.
import { ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { Exchange } from '../lib/exchange'
import { llmEvents } from '../lib/exchange'
import {
  OUTCOME_LABEL,
  answerText,
  dollars,
  seconds,
  sourcesOf,
  stepsFromAnswer,
  stepsFromEvents,
  type Source,
} from '../lib/trace'
import { Steps } from './Steps'
import './AnswerCard.css'

const KIND_NAME: Record<Source['kind'], string> = { chunk: 'Passage', claim: 'Claim', node: 'Thing', query: 'Query' }

function sourceLabel(source: Source): string {
  if (source.kind === 'chunk') return source.chunk?.context || source.id.split('#')[0]
  if (source.kind === 'query') return source.query?.sources.join(', ') || source.id
  return source.id
}

function useClock(started: number, running: boolean): number {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(performance.now()), 100)
    return () => clearInterval(timer)
  }, [running])
  return now - started
}

interface Props {
  exchange: Exchange
  active: Source | null
  onOpenSource: (source: Source) => void
}

export function AnswerCard({ exchange, active, onOpenSource }: Props) {
  const { answer, running } = exchange
  // open while the agent works, folded once it answers, unless the viewer chose otherwise
  const [chosen, setChosen] = useState<boolean | null>(null)
  const stepsOpen = chosen ?? running
  const elapsed = useClock(exchange.started, running)
  const steps = exchange.events.length ? stepsFromEvents(exchange.events) : answer ? stepsFromAnswer(answer) : []
  const sources = answer ? sourcesOf(answer) : []
  const outcome = answer?.outcome ?? null
  const calls = steps.filter((s) => s.kind === 'call').length

  return (
    <article className="answer">
      <h3 className="answer-question">{exchange.question}</h3>
      {running && (
        <p className="answer-progress">
          <span className="answer-working">Working</span> · {seconds(elapsed)} · {calls} tool {calls === 1 ? 'call' : 'calls'} so far
        </p>
      )}
      {exchange.error && <p className="answer-error">The question could not be answered: {exchange.error}</p>}
      {answer && (
        <>
          <p className="answer-text">
            {answerText(answer)}
            {sources.map((s) => (
              <button
                key={s.id}
                className={`answer-mark ${active?.id === s.id ? 'answer-mark-active' : ''}`}
                aria-label={`Source ${s.n}`}
                onClick={() => onOpenSource(s)}
              >
                {s.n}
              </button>
            ))}
          </p>
          {outcome === 'clarify' && answer.agent?.readings.length ? (
            <ul className="answer-readings">
              {answer.agent.readings.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : null}
          <p className="answer-meta">
            {outcome && <span className={`badge badge-${outcome}`}>{OUTCOME_LABEL[outcome]}</span>}
            {exchange.seconds !== null && <span>{seconds(exchange.seconds * 1000)}</span>}
            <span>{dollars(exchange.cost)}</span>
            <span>{answer.system}</span>
            {answer.screen && !answer.screen.allowed && <span>screen rule: {answer.screen.rule}</span>}
          </p>
          {sources.length > 0 && (
            <ol className="sources" aria-label="Sources">
              {sources.map((s) => (
                <li key={s.id}>
                  <button
                    className={`source ${active?.id === s.id ? 'source-active' : ''}`}
                    onClick={() => onOpenSource(s)}
                  >
                    <span className="source-n">{s.n}</span>
                    <span className="source-kind">{KIND_NAME[s.kind]}</span>
                    <span className="source-label">{sourceLabel(s)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
      {steps.length + (running ? 1 : 0) > 0 && (
        <div className="answer-steps">
          <button className="disclosure" aria-expanded={stepsOpen} onClick={() => setChosen(!stepsOpen)}>
            <ChevronRight size={18} className="disclosure-icon" aria-hidden />
            Steps
            <span className="disclosure-count">
              {calls} tool {calls === 1 ? 'call' : 'calls'}
            </span>
          </button>
          {stepsOpen && <Steps steps={steps} llm={llmEvents(exchange)} cost={exchange.cost} running={running} />}
        </div>
      )}
    </article>
  )
}
