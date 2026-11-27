# Publishing to the App Store

What App Store Connect asks for, and what to answer, for the first release.
The code side (privacy manifests, consent screen, account deletion, legal
pages) is in place; this is the part done by hand.

## Before the first upload

- **Backend live on https.** `API_DOMAIN` in the root `.env` must be
  `https://<your domain>` (a Release build refuses plain http). Use a hostname,
  not an IP address: App Review tests on IPv6-only networks.
- **Operator details.** Set `OPERATOR_NAME` and `SUPPORT_EMAIL` (and optionally
  `GOVERNING_LAW`) in `.env`, then check `https://<domain>/privacy`, `/terms` and
  `/support` read right. Have the privacy policy and terms read by someone
  qualified for where you operate. They're written from what the code does, but
  they aren't legal advice. If you keep database backups, make sure the policy's
  "replaced within 30 days" holds.
- **Paid team.** In `apps/ios/project.yml`, set `DEVELOPMENT_TEAM` to the paid
  team's ID and add `aps-environment: development` back under the app target's
  entitlements (Xcode switches it to production when you archive for
  distribution). Re-run `xcodegen`. Xcode's automatic signing registers both
  bundle IDs, the App Group and the keychain group under the new team.
- **Push.** Create an APNs key (.p8) under Certificates, IDs & Profiles → Keys,
  put it in `secrets/` at mode 600, and set `APNS_KEY_PATH`, `APNS_KEY_ID` and
  `APNS_TEAM_ID`.
- **Build number.** Raise `CURRENT_PROJECT_VERSION` in `project.yml` for every
  upload; `MARKETING_VERSION` is the version people see (1.0.0).
- **Xcode 26 / iOS 26 SDK** is required for uploads, which this project already
  targets. After archiving, open Xcode's privacy report
  (Organizer → the archive → Generate Privacy Report) and check it lists what
  the section below declares.

## App information

| Field | Answer |
|---|---|
| Name | Memory Dump (check it's free in the store first) |
| Primary category | Productivity |
| Privacy Policy URL | `https://<domain>/privacy` |
| Support URL | `https://<domain>/support` |
| Copyright | Your name or company and the year |
| Content rights | The app shows content from third-party sites the user saves, for that user only |
| Age rating | Answer the questionnaire honestly. The app has an AI that answers questions about what the user saved, and it can show text and images from any web page the user saves (unrestricted web content). Expect 13+ or higher. |
| Encryption | Already answered in the app (`ITSAppUsesNonExemptEncryption = NO`: only HTTPS and the system's Keychain) |
| License agreement | Apple's standard EULA. The service's own terms are at `/terms` |

## App Privacy ("nutrition label")

Data is **not used for tracking**. Everything below is **linked to the user**
and used for **App Functionality** only:

| Category | Type | Why |
|---|---|---|
| Contact Info | Name | Sign-up |
| Contact Info | Email Address | Sign-in |
| Contact Info | Phone Number | Optional, at sign-up or in the profile |
| User Content | Photos or Videos | Images shared to the app |
| User Content | Other User Content | Links, notes, PDFs, wiki corrections, conversations |
| Search History | Search History | Search and Ask questions |
| Identifiers | User ID | The account's ID, which every request carries |

Not collected: location (place names come from what the user saves; the map
looks coordinates up on the phone), contacts, health, financial info, browsing
history, diagnostics, usage data, advertising data. If you turn on `SENTRY_DSN`,
add **Diagnostics → Crash Data / Other Diagnostic Data**: the server's error
reports can be tied to a user.

Keep these answers, the privacy manifests (`apps/ios/Privacy/`), the consent
screen and `/privacy` in step whenever the app starts collecting or sharing
something new.

## App Review information

**Demo account.** Create one, make it an admin so the monthly allowance never
gets in the way (`docker compose exec api python -m app.admin <email>`), sign in
once on a phone, agree to AI processing, and save a handful of things: a couple
of web articles, a YouTube video, a recipe, a place (a Google Maps link), and a
photo or PDF. That way the Library, a wiki page or two, the map and Ask all have
content.

**Notes** (adapt as needed):

> Memory Dump saves links, notes, photos and PDFs the user shares to it from
> any app (Share → Memory Dump), then uses AI to summarize and organize them
> into a personal wiki and answer questions about them (Ask tab).
>
> Before any content is sent to an AI service, the app shows a consent screen
> naming each service (Anthropic, Voyage AI, and two services that read X
> posts and blocked pages). It can be withdrawn under Settings › Privacy.
> Account deletion is under Settings (avatar in the Library) › Delete account.
>
> Each account gets a free monthly amount of AI processing. Optionally, a user
> who already has an API key with an AI provider (Anthropic, OpenAI, Google,
> OpenRouter, Amazon Bedrock) can enter it in Settings › Models & usage so
> requests are billed to their own account with that provider. Nothing is sold
> in the app, and no feature of the app is unlocked by a key: the key only
> changes whose account the provider bills.
>
> The demo account has no allowance limit and already has content saved.

## Store listing

- Screenshots for 6.9" and 6.5" iPhones (and 13" iPad, since the app runs on
  iPad; or make it iPhone-only by setting `TARGETED_DEVICE_FAMILY: "1"` in
  `project.yml`).
- The description must not promise anything the app doesn't do, and mustn't
  mention other platforms or prices.

## If you distribute in the EU

App Store Connect asks whether you're a trader under the Digital Services Act.
If you are (for example you charge or plan to), your address, phone and email
are shown on the listing.
