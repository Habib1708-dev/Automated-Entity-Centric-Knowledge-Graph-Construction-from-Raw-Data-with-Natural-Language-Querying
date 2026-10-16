// The graph's legend (R223): what each node shape and edge line means, with how many of each kind the whole
// graph holds (from the build's schema), so a viewer can read the drawing without asking.
import type { SchemaView, ThingKind } from '../api/types'
import './Legend.css'

const KINDS: { kind: ThingKind; label: string }[] = [
  { kind: 'record', label: 'Table record' },
  { kind: 'individual', label: 'Named in the text' },
  { kind: 'concept', label: 'Concept' },
  { kind: 'mention', label: 'Unresolved mention' },
]

function Shape({ kind }: { kind: ThingKind }) {
  const fill = `var(--kind-${kind})`
  if (kind === 'record') return <rect x="2" y="2" width="14" height="14" rx="3" fill={fill} />
  if (kind === 'concept') return <path d="M9 1 L17 9 L9 17 L1 9 Z" fill={fill} />
  if (kind === 'mention') return <circle cx="9" cy="9" r="7" fill={fill} stroke="var(--text-3)" strokeWidth="1.5" strokeDasharray="3 2" />
  return <circle cx="9" cy="9" r="8" fill={fill} />
}

export function Legend({ schema }: { schema: SchemaView | null }) {
  const counts = new Map<ThingKind, number>()
  for (const t of schema?.things ?? []) counts.set(t.kind, (counts.get(t.kind) ?? 0) + t.count)
  return (
    <aside className="legend" aria-label="Legend">
      {KINDS.filter((k) => !schema || counts.get(k.kind)).map((k) => (
        <span key={k.kind} className="legend-item">
          <svg width="18" height="18" aria-hidden>
            <Shape kind={k.kind} />
          </svg>
          {k.label}
          {counts.get(k.kind) !== undefined && <span className="legend-count">{counts.get(k.kind)?.toLocaleString()}</span>}
        </span>
      ))}
      <span className="legend-item legend-line">
        <svg width="26" height="18" aria-hidden>
          <line x1="1" y1="9" x2="25" y2="9" stroke="var(--edge)" strokeWidth="3" />
        </svg>
        From a table
      </span>
      <span className="legend-item legend-line">
        <svg width="26" height="18" aria-hidden>
          <line x1="1" y1="9" x2="25" y2="9" stroke="var(--edge-text)" strokeWidth="3" opacity="0.6" />
        </svg>
        From the text
      </span>
    </aside>
  )
}
