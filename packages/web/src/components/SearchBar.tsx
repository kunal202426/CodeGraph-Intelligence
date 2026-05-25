import { useEffect, useState } from 'react'
import { searchCode, type SearchHit } from '../api'

// Debounced literal/semantic search. Clicking a result lifts its entity_id.
export default function SearchBar({ onSelect }: { onSelect: (id: string) => void }) {
  const [q, setQ] = useState('')
  const [semantic, setSemantic] = useState(false)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const query = q.trim()
    if (!query) return
    const handle = setTimeout(() => {
      searchCode(query, semantic, 10)
        .then((results) => {
          setHits(results)
          setOpen(true)
        })
        .catch(() => setHits([]))
    }, 250)
    return () => clearTimeout(handle)
  }, [q, semantic])

  return (
    <div className="relative flex-1">
      <div className="flex items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none placeholder:text-zinc-500 focus:border-violet-500"
          placeholder="Search the codebase…"
        />
        <label className="flex items-center gap-1 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={semantic}
            onChange={(e) => setSemantic(e.target.checked)}
          />
          semantic
        </label>
      </div>

      {open && q.trim() && hits.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-80 w-full overflow-auto rounded-md border border-zinc-700 bg-zinc-900 shadow-lg">
          {hits.map((h) => (
            <li key={h.entity_id}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onSelect(h.entity_id)
                  setOpen(false)
                }}
                className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-sm hover:bg-zinc-800"
              >
                <span className="font-medium text-zinc-100">{h.name}</span>
                <span className="text-xs text-zinc-500">{h.type}</span>
                <span className="ml-auto truncate text-xs text-zinc-500">
                  {h.file}:{h.start_line}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
