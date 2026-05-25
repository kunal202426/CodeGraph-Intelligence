// Typed wrappers over the CodeGraph FastAPI backend (see server/api.py).
// In dev, Vite proxies /api to the backend; in the packaged build they share an origin.

export interface GraphNode {
  id: string
  label: string
  type?: string
  language?: string
  start_line?: number
}

export interface GraphEdge {
  source: string
  target: string
  type: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface SearchHit {
  entity_id: string
  type: string
  name: string
  qualified_name: string
  file: string
  start_line: number
  docstring: string | null
  score: number
  retrievers: string[]
}

export interface Entity {
  entity_id: string
  type: string
  name: string
  qualified_name: string
  language: string
  file: string
  start_line: number
  end_line: number
  signature: string | null
  docstring: string | null
  raw_source: string | null
  is_exported: boolean
  is_async: boolean
  parent_id: string | null
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const health = () => getJSON<{ status: string }>('/api/health')

export const fetchModuleGraph = () => getJSON<GraphData>('/api/graph?type=module')

export const fetchEntityGraph = (file: string) =>
  getJSON<GraphData>(`/api/graph?type=entity&file=${encodeURIComponent(file)}`)

export const searchCode = (q: string, semantic = false, limit = 20) =>
  getJSON<{ results: SearchHit[] }>(
    `/api/search?q=${encodeURIComponent(q)}&semantic=${semantic}&limit=${limit}`,
  ).then((d) => d.results)

export const fetchEntity = (entityId: string) =>
  getJSON<Entity>(`/api/entity/${encodeURIComponent(entityId)}`)

export interface AskHandlers {
  onToken: (token: string) => void
  onError?: (message: string) => void
  onDone?: () => void
}

// POST /api/ask and consume the SSE stream (data: {token|error|done}).
export async function askStream(
  query: string,
  handlers: AskHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
    signal,
  })
  if (!res.ok || !res.body) {
    handlers.onError?.(`${res.status} ${res.statusText}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const event = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const dataLine = event.split('\n').find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(5).trim()) as {
        token?: string
        error?: string
        done?: boolean
      }
      if (payload.token !== undefined) handlers.onToken(payload.token)
      else if (payload.error) handlers.onError?.(payload.error)
      else if (payload.done) handlers.onDone?.()
    }
  }
}
