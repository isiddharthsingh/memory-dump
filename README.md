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

Migrations run automatically at api startup. Smoke test:

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

Voyage's free tier caps you at 3 requests/min — add a payment method on
the Voyage dashboard if you plan to ingest more than a couple of items
back-to-back. The 200M free tokens still apply.

## Mobile — quickstart

```bash
cd apps/mobile
npm install --legacy-peer-deps   # SDK 54 has a transitive react peer-dep conflict
npx expo prebuild                # regenerates ios/ + android/ (both gitignored)
npx expo run:ios                 # local simulator build (needs Xcode + iOS SDK)
# or:
npx eas build --profile development --platform ios   # cloud build, gives .ipa
```

After the dev build launches, set the **Server URL** and **API Token** in
the in-app Settings tab. The share-sheet flow only works on a real device
(or via EAS dev builds); the iOS simulator can't host a Share Extension.

See [`apps/mobile/README.md`](apps/mobile/README.md) for detailed setup,
Xcode platform requirements, and troubleshooting.

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
→ Claude Opus for the answer.

## Stack

- **Backend**: Python 3.12, FastAPI, async SQLAlchemy 2 (`asyncpg`),
  Postgres 16 + pgvector (HNSW), Redis + arq workers, Alembic. Anthropic
  SDK (Claude Haiku for analyze, Sonnet for synthesis, Opus for /ask),
  Voyage AI (`voyage-3-large` embed, `rerank-2.5`). `yt-dlp` +
  `youtube-transcript-api` for video extraction, `trafilatura` for web.
- **Mobile**: Expo SDK 54, React 19, React Native 0.81, `expo-router` v6
  (file-based, typed routes), `expo-share-intent` v5,
  `expo-secure-store` for the token, `@tanstack/react-query` for
  data fetching, `zod`-validated API client.

## License

Personal project, no license currently granted. Contact the author for
reuse.
