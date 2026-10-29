# Architecture

This is a condensed version of the original design doc. Full plan with
research citations lives in the `plans/` directory used during planning.

## System overview

```
                       ┌──────────────────────┐
   iOS                 │  SwiftUI app          │
   Share Sheet ──URL──▶│   • Library           │
                       │   • Item detail       │
                       │   • Wiki (browse)     │
                       │   • Ask (chat)        │
                       └──────────┬────────────┘
                              HTTPS (bearer)
                                  │
                       ┌──────────▼────────────┐
                       │  FastAPI              │
                       │   POST /ingest        │
                       │   GET  /items         │
                       │   GET  /wiki          │
                       │   GET  /wiki/{slug}   │
                       │   POST /search        │
                       │   POST /ask           │
                       └──────────┬────────────┘
                                  │ enqueue
                       ┌──────────▼────────────┐
                       │  arq workers (Redis)  │
                       │  1. Ingest pipeline   │
                       │  2. Wiki synthesizer  │
                       └──────────┬────────────┘
                                  │
                       ┌──────────▼────────────┐
                       │  Postgres + pgvector  │
                       └───────────────────────┘
```

## Why hybrid retrieval, not pure embeddings

Dense embeddings alone miss queries with proper nouns and identifiers —
"that Karpathy video", "@paulg's tweet". The pipeline fuses:

1. **Voyage `voyage-3-large`** dense embeddings (semantic recall)
2. **Postgres `tsvector` / BM25** full-text (exact-name recall)
3. **Reciprocal Rank Fusion** to merge the two ranked lists
4. **Voyage `rerank-2.5`** to reorder the top-20 by relevance
5. **Claude Opus 4.7** to compose the final answer with `[item:UUID]`
   citations

## Why the wiki synthesis layer

After each ingest, a worker asks Claude to **patch** topic pages
(markdown with `[[wikilinks]]`) — never regenerate from scratch. Over
time you get an AI-maintained second brain you can browse, alongside
the inbox/ask flow. Inspired by Karpathy's April 2026 LLM-wiki gist.

The retrieval pipeline searches both `item_chunks` and
`topic_page_chunks` in the same fusion, so thematic queries hit the
synthesized pages and specific recall hits raw items.

## Lexical-anchor enrichment

The ingest analysis prompt extracts `people`, `handles`,
`brands_products`, and `topics` as separate arrays. These are indexed
with GIN and also folded into the `tsvector`, so name-based queries
match deterministically.

## Schema

See `backend/alembic/versions/0001_initial.py` — `items`,
`item_chunks`, `topic_pages`, `topic_page_chunks` with HNSW vector
indexes and GIN full-text indexes.
