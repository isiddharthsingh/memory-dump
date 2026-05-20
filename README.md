# memory-dump

A personal "memory dump" mobile app. I share any link from my phone —
Instagram reels, YouTube videos, Twitter posts, blog articles — and an
AI extracts the content, analyzes it, and lets me find it later by
asking natural-language questions.

Status: **early development.** Phase 1 (backend skeleton) is in. See
[`docs/architecture.md`](docs/architecture.md) for the full design.

## What it does

1. I tap **Share → memory-dump** on any URL in iOS or Android.
2. The backend extracts the content (YouTube transcript, article body,
   tweet text, image caption + vision description for Instagram).
3. Claude summarizes it, tags it, and pulls out named entities
   (people, handles, brands, topics).
4. Voyage AI embeds the content into pgvector for semantic search,
   alongside Postgres full-text search for exact-name queries.
5. A second AI pass folds the new item into an AI-maintained
   **wiki** of topic pages — the "second brain" layer inspired by
   Karpathy's LLM-wiki pattern.
6. I open the app and ask things like *"that Karpathy video on async
   runtimes — what were the key points?"* and get an answer with
   citations back to the original sources.

## Repo layout

```
memory-dump/
├── apps/mobile/        # Expo app (iOS + Android)
├── backend/            # FastAPI + Postgres + Redis + arq workers
└── docs/
```

## Backend quickstart

```bash
cd backend
cp ../.env.example ../.env       # fill in API keys
docker compose up --build         # api on :8000, postgres on :5432
```

Smoke test:

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}

curl -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $MEMORY_DUMP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/article"}'
```

## Roadmap

- [x] Phase 1 — Backend skeleton: FastAPI, Postgres + pgvector,
      Alembic schema, auth, `/ingest` stub
- [ ] Phase 2 — Extractors (YouTube, web) + Claude analyze + Voyage embed
- [ ] Phase 3 — Mobile MVP (Inbox + Item detail + Share Sheet)
- [ ] Phase 4 — `/ask` hybrid RAG + Twitter/Instagram extractors
- [ ] Phase 5 — Wiki synthesis layer + Wiki tab
- [ ] Phase 6 — Polish + nightly compaction
