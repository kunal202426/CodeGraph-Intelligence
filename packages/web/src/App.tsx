import { useCallback, useEffect, useState } from 'react'
import { health } from './api'
import Graph from './components/Graph'
import SearchBar from './components/SearchBar'
import EntityPanel from './components/EntityPanel'
import ChatPanel from './components/ChatPanel'

// App shell. Regions: SearchBar (top), Graph (left), ChatPanel (right → T6.5),
// EntityPanel (bottom). Selection (an entity_id) is shared across them.

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
        <SearchBar onSelect={handleSelect} />
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
            <Graph onSelect={handleSelect} selectedId={selected} />
          </div>
        </section>

        <aside className="flex w-96 min-h-0 flex-col p-4">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Ask the codebase
          </h2>
          <div className="min-h-0 flex-1">
            <ChatPanel onSelect={handleSelect} />
          </div>
        </aside>
      </main>

      <footer className="h-44 shrink-0 border-t border-zinc-800 p-4">
        <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Entity details
        </h2>
        <div className="h-[calc(100%-1.5rem)] rounded-md border border-zinc-800 p-3">
          <EntityPanel entityId={selected} />
        </div>
      </footer>
    </div>
  )
}

export default App
