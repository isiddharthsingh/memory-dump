# memory-dump

A personal "memory dump" mobile app. I share any link from my phone —
Instagram reels, YouTube videos, Twitter posts, blog articles — and an
AI extracts the content, analyzes it, and lets me find it later by
asking natural-language questions.

## What it does

1. I tap **Share → Memory Dump** on any URL, PDF or image on iOS.
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
├── apps/ios/                    # SwiftUI app + Share Extension (XcodeGen project.yml)
├── backend/                     # FastAPI + Postgres + Redis + arq workers
└── docs/architecture.md
```

## Backend — quickstart

```bash
cp .env.example .env             # fill in the AI keys
cd backend
docker compose up --build
```

This starts Postgres, Redis, the SuperTokens core (accounts and sessions), the
api and the worker. Migrations run automatically at api startup. Smoke test:

```bash
curl http://localhost:8000/healthz                                       # {"status":"ok"}

# sign up; the access token comes back in the st-access-token header
TOKEN=$(curl -s -D - -o /dev/null -X POST http://localhost:8000/auth/signup \
  -H "st-auth-mode: header" -H "Content-Type: application/json" \
  -d '{"formFields":[{"id":"email","value":"me@example.com"},{"id":"password","value":"a-long-pass-123"}]}' \
  | awk 'tolower($1)=="st-access-token:" {print $2}' | tr -d '\r')

curl -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# poll until ready
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/items | jq '.[0].status'

curl -X POST http://localhost:8000/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"what music video did I save?"}'
```

Access tokens last 15 minutes; `POST /auth/session/refresh` with
`Authorization: Bearer <st-refresh-token>` gets a new pair. To manage users,
open <http://localhost:8000/auth/dashboard>; it shows the command for creating
the first dashboard login.

Voyage's free tier caps you at 3 requests/min — add a payment method on
the Voyage dashboard if you plan to ingest more than a couple of items
back-to-back. The 200M free tokens still apply.

## iOS — quickstart

```bash
brew install xcodegen
cd apps/ios
xcodegen                         # generates MemoryDump.xcodeproj from project.yml
open MemoryDump.xcodeproj
```

Connect your iPhone and press Run. The project signs with the free Personal
Team set in `project.yml`, so the install lasts 7 days; run it again from
Xcode to renew it. Sign in
with your server address, email and password. For the backend running on
your Mac, use `http://<your-mac>.local:8000` with the phone on the same Wi-Fi
(`scutil --get LocalHostName` prints the name), and allow Local Network access
when asked. Share any link, PDF or image
to **Memory Dump**: the extension saves it without leaving the app you were
in, and keeps it for later if you're offline.

Push needs a paid Apple Developer account. Once you have one, set
`DEVELOPMENT_TEAM` in `project.yml` to the paid team, add
`aps-environment: development` back to the app's entitlements, and re-run
`xcodegen`. Then create an APNs key (.p8) under Certificates, IDs & Profiles
→ Keys, put it in `secrets/` at the repo root and fill the `APNS_*` values in
`.env`.

## API

| Method | Path                  | Purpose                                 |
|--------|-----------------------|-----------------------------------------|
| GET    | `/healthz`            | liveness (no auth)                      |
| POST   | `/auth/signup`        | create an account (SuperTokens, no auth) |
| POST   | `/auth/signin`        | sign in (SuperTokens, no auth)          |
| POST   | `/auth/session/refresh` | new tokens for the refresh token      |
| POST   | `/auth/signout`       | end this session                        |
| GET    | `/auth/me`            | the signed-in account                   |
| DELETE | `/auth/me`            | delete the account and everything in it |
| POST   | `/ingest`             | submit a URL                            |
| GET    | `/items`              | list (filter by `status`, `tag`, `source`) |
| GET    | `/items/tags`         | tag counts for the chip filter         |
| GET    | `/items/{id}`         | item detail                             |
| POST   | `/items/{id}/retry`   | re-queue a failed item                  |
| DELETE | `/items/{id}`         | delete                                  |
| POST   | `/search`             | hybrid retrieval (returns SearchHits)   |
| POST   | `/ask`                | RAG answer with citations               |
| GET    | `/chats`              | Ask history: each chat's questions      |
| GET    | `/chats/{id}`         | one chat with its answers               |
| PUT    | `/chats/{id}`         | save a chat (the app picks the id)      |
| DELETE | `/chats/{id}`         | delete a chat                           |
| GET    | `/wiki`               | list topic pages                        |
| GET    | `/wiki/{slug}`        | topic page markdown                     |

Everything except `/healthz`, sign-up, sign-in and refresh takes
`Authorization: Bearer <access token>`. Send `st-auth-mode: header` with sign-up
and sign-in so the tokens come back as `st-access-token` and `st-refresh-token`
response headers.

## Architecture

See [`docs/architecture.md`](docs/architecture.md).

```
                       ┌──────────────────────┐
   iOS                 │  SwiftUI app          │
   Share Sheet ──URL──▶│   Library · Wiki · Ask│
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
- **iOS**: SwiftUI (iOS 26+, Swift 6), a Share Extension that uploads
  directly, Keychain sharing and an App Group between the two, `URLSession`
  streaming for Ask, `SFSpeechRecognizer` for voice, APNs for push. No
  third-party packages. Project generated by XcodeGen.

## License

Personal project, no license currently granted. Contact the author for
reuse.
