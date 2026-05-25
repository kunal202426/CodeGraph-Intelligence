import { useCallback, useEffect, useState } from 'react'
import { health } from './api'
import Graph from './components/Graph'

// App shell (T6.2). The four regions are filled in by later tasks:
//   - SearchBar (top)        → T6.4
//   - Graph (left, D3)       → T6.3
//   - ChatPanel (right)      → T6.5
//   - EntityPanel (bottom)   → T6.4

function App() {
  const [online, setOnline] = useState<boolean | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const handleSelect = useCallback((id: string) => setSelected(id), [])

  useEffect(() => {
    health()
      .then(() => setOnline(true))
      .catch(() => setOnline(false))
  }, [])

  return (
    <div className="flex h-full flex-col bg-zinc-950 text-zinc-100">
      <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3">
        <span className="text-lg font-semibold tracking-tight">CodeGraph</span>
        <input
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none placeholder:text-zinc-500 focus:border-violet-500"
          placeholder="Search the codebase…  (T6.4)"
          disabled
        />
        <span
          className="text-xs"
          title={online === null ? 'checking…' : online ? 'API reachable' : 'API offline'}
        >
          {online === null ? '…' : online ? '🟢 API' : '🔴 API'}
        </span>
      </header>

      <main className="flex min-h-0 flex-1">
        <section className="min-h-0 flex-1 border-r border-zinc-800 p-4">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Module graph
          </h2>
          <div className="h-[calc(100%-1.5rem)] overflow-hidden rounded-md border border-zinc-800">
            <Graph onSelect={handleSelect} />
          </div>
        </section>

        <aside className="flex w-96 min-h-0 flex-col p-4">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Ask the codebase
          </h2>
          <div className="grid flex-1 place-items-center rounded-md border border-dashed border-zinc-800 text-sm text-zinc-600">
            AI chat panel — T6.5
          </div>
        </aside>
      </main>

      <footer className="h-40 shrink-0 border-t border-zinc-800 p-4">
        <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Entity details
        </h2>
        <div className="grid h-[calc(100%-1.5rem)] place-items-center rounded-md border border-dashed border-zinc-800 text-sm text-zinc-600">
          {selected ? (
            <code className="text-violet-300">{selected}</code>
          ) : (
            'Select a node — details panel lands in T6.4'
          )}
        </div>
      </footer>
    </div>
  )
}

export default App
