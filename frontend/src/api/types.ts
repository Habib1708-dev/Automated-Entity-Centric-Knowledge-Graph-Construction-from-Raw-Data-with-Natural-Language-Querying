// The API's models under short names (R223). Every type comes from schema.ts, which `npm run api` generates
// from the backend's own pydantic models (src/kgbuilder/web/openapi.py), so a changed field breaks the build
// here instead of the page at run time. Nothing is declared by hand.
import type { components } from './schema'

type Schemas = components['schemas']

export type DatasetsView = Schemas['DatasetsView']
export type DatasetInfo = Schemas['DatasetInfo']
export type SchemaView = Schemas['SchemaView']
export type Subgraph = Schemas['Subgraph']
export type Thing = Schemas['Thing']
export type ThingKind = Thing['kind']
export type ThingDetail = Schemas['ThingDetail']
export type Fact = Schemas['Fact']
export type ChunkView = Schemas['ChunkView']
export type SourceRef = Schemas['SourceRef']

export type SystemAnswer = Schemas['SystemAnswer']
export type AgentTrace = Schemas['AgentTrace']
export type AgentCall = Schemas['AgentCall']
export type CitationCheck = Schemas['CitationCheck']
export type QueryRecord = Schemas['QueryRecord']
export type ShownChunk = Schemas['ShownChunk']
export type Outcome = NonNullable<SystemAnswer['outcome']>

export type StreamEvent = Schemas['StreamEvent']
export type ModelEvent = Schemas['ModelEvent']
export type CallEvent = Schemas['CallEvent']
export type SentBackEvent = Schemas['SentBackEvent']
export type LLMEvent = Schemas['LLMEvent']
export type AnswerEvent = Schemas['AnswerEvent']
export type ErrorEvent = Schemas['ErrorEvent']
