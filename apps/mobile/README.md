# memory-dump · mobile (Expo)

Cross-platform iOS + Android client. Share-sheet drops links into the
backend; inbox / wiki / ask consume the backend API.

**Stack:** Expo SDK 54, React 19, React Native 0.81, `expo-router` v6
(typed routes), `expo-share-intent` v5, `expo-secure-store` for the
auth token, `@tanstack/react-query`, `zod`-validated API client.

## Local setup

```bash
cd apps/mobile
npm install --legacy-peer-deps     # SDK 54 has a transitive react peer-dep conflict
```

The `--legacy-peer-deps` flag is required: `expo-router@6.1.x` pulls in
`react-server-dom-webpack@~19.2.6` (peers `react@^19.2.6`) while
`react@19.0.0` (pinned by Expo) doesn't satisfy that range. The
mismatch is harmless at runtime but breaks `npm install` without the flag.

`ios/` and `android/` are gitignored — regenerate them with prebuild:

```bash
npx expo prebuild                  # writes ios/ and android/
```

Then pick one of three dev-build paths:

### Path A — iOS Simulator (no Apple Developer account)

Requires Xcode (currently tested with **26.5**) and the matching iOS
simulator platform installed (Xcode → Settings → Platforms → iOS 26.5 →
Get). Without the matching platform, `xcodebuild` rejects every
destination with "iOS X.Y is not installed" even if you have older
runtimes.

```bash
npx expo run:ios
```

First compile is ~5–10 min. Subsequent runs are incremental. The
simulator can test everything *except* the iOS Share Extension —
you'll see the app, but tapping "memory-dump" in another app's share
sheet won't work on the simulator.

### Path B — Local Android emulator / device

```bash
npx expo run:android
```

Needs Android Studio + an emulator or USB-connected device.

### Path C — EAS cloud build (real iOS device)

```bash
npx eas login
npx eas build --profile development --platform ios
# install the resulting .ipa on a registered device
```

Requires an Expo account and (for real-device install) an Apple
Developer Program membership. This is the only path that exercises the
real share-sheet flow end-to-end.

## Running it

Once a dev build is installed on your device or simulator:

```bash
npx expo start --dev-client
```

Open the dev client. In **Settings**, enter:

- **Server URL:** `http://localhost:8000` (simulator) or
  `http://<your-laptop-LAN-ip>:8000` (real device on the same Wi-Fi).
- **API Token:** the `MEMORY_DUMP_API_TOKEN` you put in `.env`.

Tap **Test connection** to confirm the backend is reachable.

## Troubleshooting

- **`xcodebuild` says "iOS X.Y is not installed"** — open Xcode →
  Settings → Platforms and download the iOS runtime that matches your
  Xcode version. Xcode 26.x bundles its SDK but the simulator runtime
  needs an explicit download.
- **Metro serves a stale bundle / "old" UI after edits** — if you hit
  a syntax error mid-edit, Metro can freeze on it; touching the file
  and `xcrun simctl terminate <UDID> com.siddharthsingh.memorydump`
  + relaunch forces a fresh bundle.
- **App icon doesn't update after replacing `assets/icon.png`** — iOS
  bakes the icon into the asset catalog at compile time. Re-run
  `npx expo prebuild` (or copy into
  `ios/memorydump/Images.xcassets/AppIcon.appiconset/`) then rebuild.
- **"Unmatched Route" after sharing a URL** — the share-intent native
  hand-off uses `memorydump://dataUrl=memorydumpShareKey`;
  `app/+native-intent.ts` rewrites it to `/shareintent` before
  expo-router's matcher runs.
- **Voyage rate limit "3 RPM"** — the free tier caps ingest
  throughput. Add a payment method on the Voyage dashboard; the 200M
  free tokens still apply.

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
