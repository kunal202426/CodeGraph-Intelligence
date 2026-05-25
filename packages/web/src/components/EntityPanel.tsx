import { useEffect, useState } from 'react'
import { fetchEntity, type Entity } from '../api'

interface Result {
  id: string
  entity: Entity | null
  error: string | null
}

// Shows the full UIR record for the selected entity_id. State is written only
// from the async fetch (tagged with the id it belongs to), so a stale or
// in-flight selection renders "Loading…" without a synchronous reset.
export default function EntityPanel({ entityId }: { entityId: string | null }) {
  const [result, setResult] = useState<Result | null>(null)

  useEffect(() => {
    if (!entityId) return
    let cancelled = false
    fetchEntity(entityId)
      .then((e) => !cancelled && setResult({ id: entityId, entity: e, error: null }))
      .catch(
        (e: unknown) =>
          !cancelled &&
          setResult({ id: entityId, entity: null, error: e instanceof Error ? e.message : String(e) }),
      )
    return () => {
      cancelled = true
    }
  }, [entityId])

  if (!entityId) {
    return (
      <div className="grid h-full place-items-center text-sm text-zinc-600">
        Select a node or a search result.
      </div>
    )
  }
  if (!result || result.id !== entityId) {
    return <div className="grid h-full place-items-center text-sm text-zinc-600">Loading…</div>
  }
  if (result.error) {
    return <div className="grid h-full place-items-center text-sm text-red-400">{result.error}</div>
  }
  const entity = result.entity as Entity

  return (
    <div className="h-full overflow-auto text-left">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-semibold text-zinc-100">{entity.name}</span>
        <span className="text-xs text-zinc-500">{entity.type}</span>
        {entity.is_async && <span className="text-xs text-violet-400">async</span>}
        <span className="ml-auto text-xs text-zinc-500">
          {entity.file}:{entity.start_line}-{entity.end_line}
        </span>
      </div>
      {entity.signature && (
        <pre className="mt-2 overflow-auto rounded bg-zinc-900 p-2 text-xs text-zinc-200">
          {entity.signature}
        </pre>
      )}
      {entity.docstring && <p className="mt-2 text-xs text-zinc-400">{entity.docstring}</p>}
      {entity.raw_source && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-zinc-900 p-2 text-xs text-zinc-300">
          {entity.raw_source}
        </pre>
      )}
    </div>
  )
}
