---
description: Generate natural-language summaries for indexed code entities (no API key) and feed them into CodeGraph's semantic search.
---

You are enriching the CodeGraph index with per-entity summaries, using your own
reasoning instead of a paid API. This makes semantic search dramatically better:
a summary can contain concept words ("authentication", "rate limiting", "retry
backoff") that never appear in the code itself, so future searches find the right
entity by *meaning*.

Work in a loop until every entity is summarized:

1. Call the **`get_unsummarized_entities`** MCP tool (start with `limit: 20`).
2. For each returned entity, read its `signature` and `source_preview` and write
   **one short, information-dense sentence** describing **what it does and why it
   exists** — not how. Name the concepts a developer would search for. Avoid
   restating the function name. Examples:
   - `Validates a user's credentials against the store and issues a session token.`
   - `Debounces filesystem events so rapid saves trigger a single re-index.`
3. Call **`store_summaries`** with a list of `{entity_id, summary}` for that batch.
   It persists the summaries and immediately re-embeds those entities.
4. Look at the `remaining` count from step 1. If it is greater than 0, repeat from
   step 1. Stop when a batch returns `count: 0`.

Guidelines:
- Keep each summary to a single line (~15-25 words). Density beats length.
- If `source_preview` is truncated and you genuinely cannot tell what an entity
  does, call `get_entity_context` for that one `entity_id` to see its full body.
- Do not invent behavior. If an entity is trivial (a getter, a constant holder),
  say so plainly.
- Process in batches of ~20 so each `store_summaries` re-embed stays fast.

When done, call **`index_status`** and report the `summarized` / `entities`
coverage so the user can see progress.
