import { useEffect, useRef, useState, type ReactNode } from 'react'
import { askStream } from '../api'

interface Message {
  role: 'user' | 'assistant'
  text: string
}

// Render assistant text with [entity_id] citations as clickable spans.
function renderWithCitations(text: string, onSelect: (id: string) => void): ReactNode[] {
  const pattern = /\[((?:py|ts|js):[^\]]+)\]/g
  const out: ReactNode[] = []
  let last = 0
  let key = 0
  let m: RegExpExecArray | null
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const id = m[1]
    out.push(
      <button
        key={`c${key++}`}
        type="button"
        onClick={() => onSelect(id)}
        className="text-violet-400 underline decoration-dotted hover:text-violet-300"
      >
        [{id}]
      </button>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function ChatPanel({ onSelect }: { onSelect: (id: string) => void }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  function appendToLast(token: string) {
    setMessages((prev) => {
      const copy = [...prev]
      const last = copy[copy.length - 1]
      copy[copy.length - 1] = { ...last, text: last.text + token }
      return copy
    })
  }

  function submit() {
    const query = input.trim()
    if (!query || busy) return
    setInput('')
    setBusy(true)
    setMessages((prev) => [...prev, { role: 'user', text: query }, { role: 'assistant', text: '' }])
    askStream(query, {
      onToken: appendToLast,
      onError: (msg) => appendToLast(`\n[error: ${msg}]`),
    })
      .catch((e: unknown) => appendToLast(`\n[error: ${e instanceof Error ? e.message : String(e)}]`))
      .finally(() => setBusy(false))
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto rounded-md border border-zinc-800 p-3 text-left text-sm">
        {messages.length === 0 ? (
          <p className="text-zinc-600">Ask a question about the codebase…</p>
        ) : (
          messages.map((msg, i) => (
            <div key={i} className={msg.role === 'user' ? 'text-zinc-300' : 'text-zinc-100'}>
              <span className="mr-1 text-xs uppercase text-zinc-500">
                {msg.role === 'user' ? 'you' : 'codegraph'}
              </span>
              <span className="whitespace-pre-wrap">
                {msg.role === 'assistant'
                  ? renderWithCitations(msg.text, onSelect)
                  : msg.text}
                {busy && msg.role === 'assistant' && i === messages.length - 1 && (
                  <span className="animate-pulse text-zinc-500">▋</span>
                )}
              </span>
            </div>
          ))
        )}
      </div>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
          placeholder="How does authentication work?"
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm outline-none placeholder:text-zinc-500 focus:border-violet-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
        >
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
