"""The privacy policy, terms of use and support page, as plain HTML at /privacy,
/terms and /support: the addresses App Store Connect and the app link to.

They are served by the API itself, so they are up whenever the app works and
change with the code that does what they describe. Who runs the service and
how to reach them come from settings (OPERATOR_NAME, SUPPORT_EMAIL,
GOVERNING_LAW). When the text changes, change UPDATED too.

These are a starting point written from what this code does, not legal
advice: have them read by someone qualified for where you operate.
"""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings

router = APIRouter(include_in_schema=False)

UPDATED = "November 27, 2026"

_STYLE = """
:root { color-scheme: light dark; --ink: #1c1b19; --muted: #5f5b53; --page: #fbfaf7;
  --rule: #e4e0d7; --link: #2f5d8a; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #ece9e2; --muted: #a8a397; --page: #161514; --rule: #2c2a27; --link: #8db7e0; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 17px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
main { max-width: 44rem; margin: 0 auto; padding: 2.5rem 1rem 4rem; }
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
p.meta { color: var(--muted); margin: 0 0 1.5rem; }
a { color: var(--link); }
ul { padding-left: 1.25rem; }
li { margin: .3rem 0; }
nav { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 3rem; color: var(--muted); }
"""


def _who() -> tuple[str, str, str]:
    """The operator's name, a contact sentence, and the email as a link."""
    s = get_settings()
    name = html.escape(s.operator_name.strip() or "the operator of Memory Dump")
    email = s.support_email.strip()
    if email:
        link = f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>'
        contact = f"Write to {link}."
    else:
        link = "the address on the App Store listing"
        contact = "Use the contact details on the App Store listing."
    return name, contact, link


def _page(title: str, body: str) -> HTMLResponse:
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Memory Dump</title>
<style>{_STYLE}</style>
</head>
<body><main>
{body}
<nav><a href="/privacy">Privacy Policy</a><a href="/terms">Terms of Use</a><a href="/support">Support</a></nav>
</main></body>
</html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    name, contact, _ = _who()
    sentry = (
        "<li><strong>Sentry</strong> receives reports of errors in our server, "
        "without the content of requests.</li>"
        if get_settings().sentry_dsn
        else ""
    )
    body = f"""
<h1>Privacy Policy</h1>
<p class="meta">Last updated {UPDATED}</p>

<p>Memory Dump keeps the links, notes, photos and PDFs you save, organizes them
with AI, and answers questions about them. It is run by {name} ("we"). This
policy says what we collect, why, who else receives it, and how you control
it.</p>

<h2>What we collect</h2>
<ul>
<li><strong>Your account:</strong> your email address, your password (stored
only as a one-way Argon2id hash), your name, and your phone number if you choose
to add one.</li>
<li><strong>What you save:</strong> links and the text of the pages they point
to, notes, photos and PDFs you share (kept with a small preview image), and
what we make from them: titles, summaries, tags, places, your wiki pages, and
search indexes (embeddings).</li>
<li><strong>What you ask:</strong> your searches, your questions in Ask and the
answers, and the conversation history the app keeps for you. Voice questions
are turned into text on your iPhone, or by Apple when on-device recognition
isn't available; we receive only the text.</li>
<li><strong>Your own API keys,</strong> if you add any: stored encrypted, used
only for your requests, and never shown back except their last characters.</li>
<li><strong>Technical data:</strong> your device's push notification token, the
app's build number, your IP address (used to limit sign-in attempts), a record
of how much AI processing your account used each day, and server logs (which
can include the addresses of links you save). Logs are kept for a limited time
for security and fixing problems, then deleted.</li>
</ul>
<p>We don't use advertising or analytics SDKs, don't track you across other
apps or websites, and don't sell your data.</p>

<h2>How we use it</h2>
<p>Only to provide Memory Dump to you: to save and organize what you share, to
search it and answer your questions, to send you the notifications you allow,
to keep accounts secure and the service running, and to limit how much free
AI processing each account uses. We don't use your content to train AI models
or to advertise to you.</p>

<h2>Who else receives it</h2>
<p>With your permission, which the app asks for before anything is sent, your
content goes to these services so they can process it for you:</p>
<ul>
<li><strong>Anthropic</strong> (Claude): reads what you save, writes your wiki,
and answers your questions.</li>
<li><strong>Voyage AI</strong>: turns your saves and searches into search
indexes and ranks results.</li>
<li><strong>The provider of any API key you add</strong> (OpenAI, Google,
OpenRouter or Amazon Web Services Bedrock), instead of Anthropic, under your own
account with them.</li>
<li><strong>FxTwitter</strong> (fxtwitter.com): receives the link of a post on X
you save, to read it.</li>
<li><strong>Jina AI</strong> (r.jina.ai): receives a link you save when the site
blocks our direct request, to read the page.</li>
</ul>
<p>Each of them handles it under its own privacy terms. Also involved:</p>
<ul>
<li><strong>The websites your links point to:</strong> our server fetches each
page (the site sees our server, not you). To show a link's preview image, the
app loads it straight from the site that hosts it, which sees your IP address
as with any web request.</li>
<li><strong>Apple</strong> delivers push notifications, which can include the
title of something you saved.</li>
{sentry}
<li><strong>Our hosting provider</strong> runs the servers and storage that
hold your data.</li>
</ul>
<p>We may disclose data if the law requires it. If the service is ever
transferred to someone else, this policy keeps applying to the data you gave
us, and we'll tell you first. These services may process data outside your
country, including in the United States.</p>

<h2>How long we keep it, and deleting it</h2>
<ul>
<li>Your saves, wiki and conversations are kept until you delete them or your
account.</li>
<li>Deleting a save removes it from your library and search at once, and from
your wiki within moments. Deleting a conversation removes it from History.</li>
<li><strong>Delete your account</strong> in the app under Settings › Delete
account. Everything you saved, your wiki, conversations, keys and devices are
deleted at once; stored files follow within an hour. Backups, if any, are
replaced on a rolling basis within 30 days.</li>
</ul>

<h2>Your choices and rights</h2>
<ul>
<li><strong>Withdraw permission</strong> for the AI services anytime under
Settings › AI processing. From then on nothing of yours is sent to them, and
Memory Dump can't save or answer anything new until you allow it again.</li>
<li><strong>See and take your data:</strong> Settings › Export for Obsidian
downloads all your saves and wiki pages.</li>
<li><strong>Correct it:</strong> edit your name and phone number in Settings,
and correct any wiki page from the page itself.</li>
<li><strong>Notifications and microphone</strong> are optional and can be
turned off in iOS Settings.</li>
</ul>
<p>Depending on where you live (for example under the GDPR in the EU and UK, or
California's CCPA), you may have further rights, such as to object to or
restrict processing or to complain to a data protection authority. For
everything else, or to exercise a right, contact us. Where the GDPR applies we
process your data to provide the service you signed up for, and with your
consent for the AI services.</p>

<h2>Children</h2>
<p>Memory Dump isn't meant for children under 13 (or the minimum age for
consent in your country), and we don't knowingly collect their data.</p>

<h2>Changes</h2>
<p>We'll post any change to this policy here with a new date, and tell you in
the app if it matters.</p>

<h2>Contact</h2>
<p>{contact}</p>
"""
    return _page("Privacy Policy", body)


@router.get("/terms", response_class=HTMLResponse)
async def terms() -> HTMLResponse:
    name, contact, _ = _who()
    law = get_settings().governing_law.strip()
    governing = (
        f"<p>These terms are governed by the laws of {html.escape(law)}, except where "
        "the law where you live gives you rights that can't be waived.</p>"
        if law
        else ""
    )
    body = f"""
<h1>Terms of Use</h1>
<p class="meta">Last updated {UPDATED}</p>

<p>These terms apply to Memory Dump, the app and the service behind it, run by
{name} ("we"). By creating an account you agree to them and to the
<a href="/privacy">Privacy Policy</a>.</p>

<h2>Your account</h2>
<p>You must be at least 13 years old, or the minimum age for consent where you
live. Keep your password to yourself; you're responsible for what happens under
your account. One account per person.</p>

<h2>Your content</h2>
<p>What you save stays yours. You let us store, copy and process it, including
sending it to the AI services listed in the Privacy Policy, only to provide
Memory Dump to you. Save only what you have the right to keep, and nothing
illegal.</p>

<h2>Acceptable use</h2>
<p>Don't use Memory Dump to break the law or harm others; don't try to get into
other people's accounts or our systems, overload or disrupt the service, or get
around its limits (for example by creating accounts to get more free AI
processing); and don't use it to collect other people's data without their
permission.</p>

<h2>AI answers</h2>
<p>Summaries, wiki pages and answers are written by AI from what you saved. They
can be incomplete or wrong. Check anything that matters, and don't rely on them
as professional advice.</p>

<h2>Free use and your own keys</h2>
<p>Each account gets a monthly amount of free AI processing, which we may change.
If you add your own API key, requests made with it are billed to you by that
provider under their terms, and you're responsible for those charges.</p>

<h2>Links to other sites</h2>
<p>Pages and posts you save belong to their owners. We fetch them on your
behalf for your own use.</p>

<h2>Changes and ending</h2>
<p>We may change or stop parts of the service. If we shut it down, we'll give
you notice and time to export your data. You can delete your account anytime in
the app. We may suspend accounts that break these terms.</p>

<h2>No warranty, limited liability</h2>
<p>Memory Dump is provided "as is", without warranties of any kind. To the
extent the law allows, we aren't liable for indirect or consequential losses,
or for lost data, and our total liability is limited to the amount you paid us
in the past twelve months.</p>
{governing}

<h2>The App Store</h2>
<p>If you got the app from Apple's App Store, Apple's standard Licensed
Application End User License Agreement also applies to the app. Apple isn't
responsible for the app or the service, or for support.</p>

<h2>Contact</h2>
<p>{contact}</p>
"""
    return _page("Terms of Use", body)


@router.get("/support", response_class=HTMLResponse)
async def support() -> HTMLResponse:
    _, contact, link = _who()
    body = f"""
<h1>Support</h1>
<p class="meta">Memory Dump</p>

<p>Questions, problems or requests about your data: {contact} We usually
reply within a few days.</p>

<h2>I forgot my password</h2>
<p>Write to {link} from the email address you signed up with, and we'll help
you get back in.</p>

<h2>A save failed</h2>
<p>Open it in your library to see why. Most can be tried again from there; a
link to a private network, a page over 5 MB, or a file over 5 MB can't be
saved.</p>

<h2>Stop sending my content to AI services</h2>
<p>Settings › AI processing › Withdraw. Memory Dump can't save or answer
anything new until you allow it again.</p>

<h2>Get a copy of my data</h2>
<p>Settings › Export for Obsidian downloads your saves and wiki pages.</p>

<h2>Delete my account</h2>
<p>Settings › Delete account. Everything is deleted at once and can't be
recovered.</p>
"""
    return _page("Support", body)
