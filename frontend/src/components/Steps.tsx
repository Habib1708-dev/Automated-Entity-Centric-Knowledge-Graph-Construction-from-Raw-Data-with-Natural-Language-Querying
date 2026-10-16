// How the agent got to its answer (R223): every step in the order it happened, each tool call with the model
// time that proposed it, its arguments, its own time and what it returned; every send-back with its reason;
// and the run's totals. Each row's bar is as long as its time against the slowest step, model time light and
// tool time in the accent, so where the seconds went reads at a glance. A row opens to its full arguments and
// the result the tool returned.
import { CornerDownLeft } from 'lucide-react'
import { useState } from 'react'
import type { LLMEvent } from '../api/types'
import { SEND_BACK_LABEL, dollars, seconds, totals, type StepRow } from '../lib/trace'
import './Steps.css'

interface Props {
  steps: StepRow[]
  llm: LLMEvent[]
  cost: number | null
  running: boolean
}

function stepMs(row: StepRow): number {
  if (row.kind === 'sent_back') return 0
  return (row.modelMs ?? 0) + (row.kind === 'call' ? row.call.latency_ms : 0)
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text // the model wrote arguments that are not JSON: code refused them, show them as written
  }
}

function CallRow({ row, slowest, last }: { row: Extract<StepRow, { kind: 'call' }>; slowest: number; last: boolean }) {
  const [open, setOpen] = useState(false)
  const { call } = row
  const model = row.modelMs ?? 0
  const outcome = call.refused
    ? `Refused: ${call.refused}`
    : `Returned ${call.returned.length} ${call.returned.length === 1 ? 'item' : 'items'}${call.note ? ` · ${call.note}` : ''}`
  return (
    <li className={`step ${last ? 'step-last' : ''}`}>
      <button className="step-row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="step-turn">{row.turn}</span>
        <span className="step-main">
          <code className={`step-tool ${call.refused ? 'step-refused' : ''}`}>{call.tool}</code>
          <span className="step-outcome">{outcome}</span>
        </span>
        <span className="step-time">
          <span className="step-bar" aria-hidden>
            <span className="step-bar-model" style={{ width: `${(model / slowest) * 100}%` }} />
            <span className="step-bar-tool" style={{ width: `${(call.latency_ms / slowest) * 100}%` }} />
          </span>
          <span>
            {row.modelMs !== null && `model ${seconds(model)} · `}tool {seconds(call.latency_ms)}
          </span>
        </span>
      </button>
      {open && (
        <div className="step-detail">
          <p className="step-label">Arguments</p>
          <pre>{prettyJson(call.arguments)}</pre>
          {row.result && (
            <>
              <p className="step-label">Result{row.cut ? ' (first 4,000 characters)' : ''}</p>
              <pre>{prettyJson(row.result)}</pre>
            </>
          )}
        </div>
      )}
    </li>
  )
}

export function Steps({ steps, llm, cost, running }: Props) {
  const slowest = Math.max(1, ...steps.map(stepMs))
  const sum = totals(steps, llm)
  return (
    <div className="steps">
      <ol className="steps-list">
        {steps.map((row, i) => {
          const last = running && i === steps.length - 1
          if (row.kind === 'call') return <CallRow key={i} row={row} slowest={slowest} last={last} />
          if (row.kind === 'sent_back')
            return (
              <li key={i} className="step step-note">
                <CornerDownLeft size={16} aria-hidden />
                {SEND_BACK_LABEL[row.reason]}
              </li>
            )
          return (
            <li key={i} className="step">
              <div className="step-row step-static">
                <span className="step-turn">{row.turn}</span>
                <span className="step-main">
                  <span className="step-outcome">
                    {!running && i === steps.length - 1 ? 'Model wrote the answer' : 'Model replied without a tool call'}
                  </span>
                </span>
                <span className="step-time">
                  <span className="step-bar" aria-hidden>
                    <span className="step-bar-model" style={{ width: `${(row.modelMs / slowest) * 100}%` }} />
                  </span>
                  <span>{seconds(row.modelMs)}</span>
                </span>
              </div>
            </li>
          )
        })}
        {running && <li className="step step-note step-working">Waiting for the model…</li>}
      </ol>
      <p className="steps-totals">
        <span>
          {sum.calls} tool {sum.calls === 1 ? 'call' : 'calls'}
          {sum.refused > 0 && ` (+${sum.refused} refused)`}
        </span>
        <span>
          <i className="key key-model" aria-hidden /> model {seconds(sum.modelMs)}
        </span>
        <span>
          <i className="key key-tool" aria-hidden /> tools {seconds(sum.toolMs)}
        </span>
        <span>
          {sum.requests} LLM requests{sum.cached > 0 && `, ${sum.cached} cached`}
        </span>
        <span>
          {sum.promptTokens.toLocaleString()} tokens in · {sum.outputTokens.toLocaleString()} out
        </span>
        {!running && <span>{dollars(cost)}</span>}
      </p>
    </div>
  )
}
