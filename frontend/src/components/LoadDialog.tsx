// Loading a dataset's graph into Neo4j (R222b): a confirmation first, since the load clears the graph Neo4j
// holds for every session and tool using it, then the recipe's steps as they run, with their times, and the
// output of a step that fails. A modal on purpose: the action is destructive and must not be half-noticed.
import { Check, LoaderCircle, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, load } from '../api/client'
import type { DatasetInfo, LoadedEvent, StepEvent } from '../api/types'
import { seconds } from '../lib/trace'
import './LoadDialog.css'

interface Props {
  dataset: DatasetInfo
  holding: string | null // the name of the dataset Neo4j holds now, if any
  onClose: () => void
  onLoaded: () => void // the load ended (well or not): the tabs' loaded state must be read again
  onBusy: (busy: boolean) => void
}

type Phase = 'confirm' | 'running' | 'ended'

export function LoadDialog({ dataset, holding, onClose, onLoaded, onBusy }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [phase, setPhase] = useState<Phase>('confirm')
  const [steps, setSteps] = useState<StepEvent[]>([])
  const [end, setEnd] = useState<LoadedEvent | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    dialog.current?.showModal()
  }, [])

  const start = async () => {
    setPhase('running')
    onBusy(true)
    try {
      await load(dataset.id, (event) => {
        if (event.event === 'loaded') setEnd(event)
        else setSteps((all) => [...all.filter((s) => s.index !== event.index), event])
      })
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e))
    } finally {
      setPhase('ended')
      onBusy(false)
      onLoaded()
    }
  }

  // a running load cannot be dismissed: closing the page's view of it would not stop it
  const close = () => phase !== 'running' && onClose()
  const failed = steps.find((s) => s.status === 'failed')

  return (
    <dialog ref={dialog} className="load" onCancel={(e) => (e.preventDefault(), close())} aria-labelledby="load-title">
      <div className="load-head">
        <h2 id="load-title">Load {dataset.name} into Neo4j</h2>
        {phase !== 'running' && (
          <button className="load-close" aria-label="Close" onClick={close}>
            <X size={20} />
          </button>
        )}
      </div>
      {phase === 'confirm' && (
        <div className="load-body">
          <p>
            This clears the graph Neo4j holds now{holding ? `, ${holding},` : ''} for everyone using it, and rebuilds{' '}
            {dataset.name} from its final build: nine steps, a few minutes.
          </p>
          <p className="load-note">
            No generation call is paid: every model answer comes from the cache. The text and card embeddings are
            requested again, a fraction of a cent.
          </p>
          <div className="load-actions">
            <button className="load-secondary" onClick={close}>
              Cancel
            </button>
            <button className="load-primary" onClick={start}>
              Load {dataset.name}
            </button>
          </div>
        </div>
      )}
      {phase !== 'confirm' && (
        <div className="load-body">
          <ol className="load-steps">
            {steps.map((s) => (
              <li key={s.index} className={`load-step load-${s.status}`}>
                {s.status === 'running' && <LoaderCircle size={18} className="load-spin" aria-hidden />}
                {s.status === 'done' && <Check size={18} aria-hidden />}
                {s.status === 'failed' && <X size={18} aria-hidden />}
                <code>kg {s.name}</code>
                <span className="load-time">{s.status === 'running' ? 'running' : seconds(s.seconds * 1000)}</span>
              </li>
            ))}
          </ol>
          {failed?.output && <pre className="load-output">{failed.output}</pre>}
          {error && <p className="load-error">{error}</p>}
          {end && <p className={end.ok ? 'load-ok' : 'load-error'}>{end.message} · {seconds(end.seconds * 1000)}</p>}
          {phase === 'ended' && (
            <div className="load-actions">
              <button className="load-primary" onClick={close}>
                Done
              </button>
            </div>
          )}
        </div>
      )}
    </dialog>
  )
}
