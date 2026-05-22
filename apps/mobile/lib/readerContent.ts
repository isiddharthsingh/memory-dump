/**
 * Parses the `raw_text` field that backend extractors emit. They produce a
 * structured plain-text blob with section headers like:
 *
 *   Title: ...
 *   Summary: ...           (webpage og:description)
 *   Body:\n...             (webpage trafilatura body)
 *   Channel: ...           (youtube)
 *   Transcript:\n...       (youtube)
 *   Description:\n...      (youtube)
 *   User note:\n...
 *   URL: ...               (fallback stub when the page is blocked)
 *
 * We pick the richest readable chunk (Body > Transcript > Description >
 * Summary) and surface the user's share-note separately. If we only have
 * the URL stub or nothing useful, return `isStub: true` so the UI can show
 * a "no reader content" state instead of a wall of metadata.
 */

export type ReaderContent = {
  body: string | null;
  note: string | null;
  isStub: boolean;
};

const BODY_PRIORITY = ["Body", "Transcript", "Description", "Summary"] as const;

export function parseReaderContent(rawText: string | null | undefined): ReaderContent {
  if (!rawText || !rawText.trim()) {
    return { body: null, note: null, isStub: true };
  }

  // Each section is separated by a blank line; the first line is the header.
  const sections = new Map<string, string>();
  for (const chunk of rawText.split(/\n\n+/)) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;
    const headerMatch = trimmed.match(/^([A-Za-z][A-Za-z ]+?):\s*(.*)$/s);
    if (!headerMatch) continue;
    const [, rawKey, rest] = headerMatch;
    const key = rawKey!.trim();
    if (!sections.has(key)) sections.set(key, rest!.trim());
  }

  let body: string | null = null;
  for (const key of BODY_PRIORITY) {
    const value = sections.get(key);
    if (value && value.length > 0) {
      body = value;
      break;
    }
  }

  const note = sections.get("User note") ?? null;
  const isStub = !body || body.length < 40;

  return { body, note, isStub };
}
