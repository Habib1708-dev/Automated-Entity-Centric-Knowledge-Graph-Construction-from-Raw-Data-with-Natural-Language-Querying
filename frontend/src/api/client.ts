// The page's calls to the web API (src/kgbuilder/web/app.py), R223: one function per route, and the reader
// of a question's event stream. Components call these and never build a URL themselves.
// Failure: a non-2xx response becomes an ApiError carrying the API's own message (FastAPI's `detail`).
import type {
  ChunkView,
  DatasetsView,
  LoadEvent,
  SchemaView,
  StreamEvent,
  Subgraph,
  Thing,
  SourceRef,
  ThingDetail,
} from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function failure(response: Response): Promise<ApiError> {
  let message = `${response.status} ${response.statusText}`
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') message = body.detail
  } catch {
    // the body was not JSON (a proxy error page): the status line is all there is to say
  }
  return new ApiError(response.status, message)
}

async function get<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))
  const response = await fetch(`/api${path}${query.size ? `?${query}` : ''}`)
  if (!response.ok) throw await failure(response)
  return response.json() as Promise<T>
}

const base = (dataset: string) => `/datasets/${encodeURIComponent(dataset)}`

export const api = {
  datasets: () => get<DatasetsView>('/datasets'),
  schema: (dataset: string) => get<SchemaView>(`${base(dataset)}/schema`),
  overview: (dataset: string, limit: number) => get<Subgraph>(`${base(dataset)}/graph`, { limit }),
  neighbourhood: (dataset: string, thing: string, limit: number) =>
    get<Subgraph>(`${base(dataset)}/neighbourhood`, { thing, limit }),
  search: (dataset: string, q: string) => get<Thing[]>(`${base(dataset)}/search`, { q, limit: 12 }),
  thing: (dataset: string, id: string) => get<ThingDetail>(`${base(dataset)}/thing`, { id, limit: 60 }),
  chunk: (dataset: string, id: string) => get<ChunkView>(`${base(dataset)}/chunk`, { id }),
  locate: async (dataset: string, sources: SourceRef[]): Promise<Record<string, string[]>> => {
    const response = await fetch(`/api${base(dataset)}/locate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sources),
    })
    if (!response.ok) throw await failure(response)
    return response.json()
  },
}

/** Split server-sent-event text into its `data:` payloads; returns the events and the unfinished tail. */
export function parseEvents<E = StreamEvent>(buffer: string): { events: E[]; rest: string } {
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events = blocks
    .map((block) => block.split('\n').find((line) => line.startsWith('data: ')))
    .filter((line): line is string => line !== undefined)
    .map((line) => JSON.parse(line.slice('data: '.length)) as E)
  return { events, rest }
}

/** POST to `path` and call `onEvent` for each server-sent event until the stream ends. */
async function stream<E>(path: string, body: unknown, onEvent: (event: E) => void): Promise<void> {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok || response.body === null) throw await failure(response)
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    const parsed = parseEvents<E>(buffer + value)
    buffer = parsed.rest
    parsed.events.forEach(onEvent)
  }
}

/** Ask `question` of `dataset` with `variant`, calling `onEvent` for each event as it arrives. Resolves when
 * the stream ends; rejects with ApiError when the API refuses the question (another one running: 409). */
export async function ask(
  dataset: string,
  question: string,
  variant: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  return stream(`${base(dataset)}/ask`, { question, variant }, onEvent)
}

/** Load `dataset`'s graph into Neo4j (R222b), calling `onEvent` for each recipe step and the end; rejects
 * with ApiError when the API refuses (a question or another load running: 409). */
export function load(dataset: string, onEvent: (event: LoadEvent) => void): Promise<void> {
  return stream(`${base(dataset)}/load`, undefined, onEvent)
}
