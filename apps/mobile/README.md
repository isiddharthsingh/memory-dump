# memory-dump · mobile (Expo)

Cross-platform iOS + Android client. Share-sheet drops links into the
backend; inbox / wiki / ask consume the backend API.

## Local setup

```bash
cd apps/mobile
npm install                        # or pnpm install
```

Expo Go cannot host the iOS Share Extension or Android send-target — you
need a dev build:

```bash
npx expo prebuild                  # generates ios/ and android/
npx eas build --profile development --platform ios     # or android
# install the resulting .ipa/.apk on your device
```

Then start the dev server:

```bash
npx expo start --dev-client
```

Open the dev client app on the device. In **Settings**, enter:

- **Server URL:** `http://<your-laptop-LAN-ip>:8000`
- **API Token:** the `MEMORY_DUMP_API_TOKEN` you put in `.env`

Tap **Test connection** to confirm the backend is reachable.

## Sharing a link

Anywhere on the phone (Safari, Twitter, YouTube, Instagram, etc.), tap
**Share → memory-dump**. The app POSTs to `/ingest`, the worker
processes the URL in the background, and the item appears in the Inbox
within ~5–15 seconds depending on the source.

## Asking questions

Open the **Ask** tab and type something like:

- *"that Karpathy video on async runtimes — what were the main points?"*
- *"have I saved anything about Rust async?"*
- *"summarize what I've been reading about LLM context windows"*

Answers cite the source items underneath. Tap a citation to jump into
the item or the wiki topic page.

## Project structure

```
apps/mobile/
├── app/
│   ├── _layout.tsx              # root layout, share-intent handler, query client
│   ├── (tabs)/
│   │   ├── _layout.tsx          # tab bar
│   │   ├── index.tsx            # Inbox
│   │   ├── wiki.tsx             # Wiki list
│   │   ├── ask.tsx              # Ask
│   │   └── settings.tsx
│   ├── item/[id].tsx
│   └── wiki/[slug].tsx
├── components/
│   ├── ItemCard.tsx
│   └── EmptyState.tsx
├── lib/
│   ├── api.ts                   # typed (zod-validated) client
│   ├── settings.ts              # SecureStore-backed config
│   ├── useIncomingShare.ts      # share-intent → /ingest
│   ├── queryClient.ts
│   └── theme.ts
├── app.json
├── eas.json
└── package.json
```
