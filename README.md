# memory-dump

A personal "memory dump" mobile app. I share any link from my phone —
Instagram reels, YouTube videos, Twitter posts, blog articles — and an
AI extracts the content, analyzes it, and lets me find it later by
asking natural-language questions.

## What it does

1. I tap **Share → memory-dump** on any URL in iOS or Android.
2. The backend extracts the content (YouTube transcript, article body,
   tweet text, image caption + vision description for Instagram).
3. Claude summarizes it, tags it, and pulls out named entities
   (people, handles, brands, topics).
4. Voyage AI embeds the content into pgvector alongside Postgres
   full-text search for exact-name queries (hybrid retrieval).
5. A second AI pass folds the new item into an AI-maintained **wiki**
   of topic pages — the "second brain" layer inspired by Karpathy's
   LLM-wiki pattern. Pages get *patched*, not regenerated.
6. I open the app and ask things like *"that Karpathy video on async
   runtimes — what were the key points?"* and get an answer with
   citations back to the original sources.

## Repo layout

```
memory-dump/
├── apps/mobile/                 # Expo app (iOS + Android, share-sheet)
├── backend/                     # FastAPI + Postgres + Redis + arq workers
└── docs/architecture.md
```

## Backend — quickstart

```bash
cp .env.example .env             # fill in MEMORY_DUMP_API_TOKEN + AI keys
cd backend
docker compose up --build
```

Smoke test:

```bash
curl http://localhost:8000/healthz                                       # {"status":"ok"}

curl -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $MEMORY_DUMP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# poll until ready
curl -s -H "Authorization: Bearer $MEMORY_DUMP_API_TOKEN" \
  http://localhost:8000/items | jq '.[0].status'

curl -X POST http://localhost:8000/ask \
  -H "Authorization: Bearer $MEMORY_DUMP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"what music video did I save?"}'
```

## Mobile — quickstart

See [`apps/mobile/README.md`](apps/mobile/README.md). TL;DR:

```bash
cd apps/mobile
npm install
npx expo prebuild
npx eas build --profile development --platform ios     # or android
npx expo start --dev-client
```

Set the server URL + bearer token in the in-app Settings tab.

## API

| Method | Path                  | Purpose                                 |
|--------|-----------------------|-----------------------------------------|
| GET    | `/healthz`            | liveness (no auth)                      |
| POST   | `/ingest`             | submit a URL                            |
| GET    | `/items`              | list (filter by `status`, `tag`, `source`) |
| GET    | `/items/tags`         | tag counts for the chip filter         |
| GET    | `/items/{id}`         | item detail                             |
| POST   | `/items/{id}/retry`   | re-queue a failed item                  |
| DELETE | `/items/{id}`         | delete                                  |
| POST   | `/search`             | hybrid retrieval (returns SearchHits)   |
| POST   | `/ask`                | RAG answer with citations               |
| GET    | `/wiki`               | list topic pages                        |
| GET    | `/wiki/{slug}`        | topic page markdown                     |

All endpoints except `/healthz` require `Authorization: Bearer <MEMORY_DUMP_API_TOKEN>`.

## Architecture

See [`docs/architecture.md`](docs/architecture.md).

```
                       ┌──────────────────────┐
   iOS / Android       │  Expo app             │
   Share Sheet ──URL──▶│   Inbox · Wiki · Ask │
                       └──────────┬────────────┘
                              HTTPS (bearer)
                                  │
                       ┌──────────▼────────────┐
                       │  FastAPI              │
                       └──────────┬────────────┘
                                  │ enqueue (arq)
                       ┌──────────▼────────────┐
                       │  workers              │
                       │  1. ingest_item       │
                       │  2. synthesize_topics │
                       │  3. compact_topics    │
                       └──────────┬────────────┘
                                  │
                       ┌──────────▼────────────┐
                       │  Postgres + pgvector  │
                       └───────────────────────┘
```

Retrieval is **hybrid**: dense (Voyage `voyage-3-large` over `pgvector`
HNSW) ∪ BM25 (`tsvector`) → Reciprocal Rank Fusion → Voyage `rerank-2.5`
→ Claude Opus 4.7 for the answer.

## Roadmap

- [x] Phase 1 — Backend skeleton (FastAPI, Postgres, Alembic, auth)
- [x] Phase 2 — Extractors (YouTube, web, Twitter, Instagram) + Claude analyze + Voyage embed
- [x] Phase 3 — Mobile MVP (Inbox + Item detail + Share Sheet + Settings)
- [x] Phase 4 — `/ask` hybrid RAG endpoint + Ask tab
- [x] Phase 5 — Wiki synthesis layer + Wiki tab
- [x] Phase 6 — Polish: retry, tag filter, nightly compaction, structured logs
