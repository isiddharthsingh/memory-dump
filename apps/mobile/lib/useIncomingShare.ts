import { useShareIntentContext } from "expo-share-intent";
import { useEffect, useRef } from "react";

import { api, ApiError } from "./api";
import { shareQueue } from "./shareQueue";

const URL_RE = /https?:\/\/\S+/i;

type SharedFile = {
  path?: string;
  uri?: string;
  fileName?: string;
  fileSize?: number;
  mimeType?: string;
  type?: string;
};

function pickFile(shareIntent: { files?: SharedFile[] | null }): SharedFile | null {
  const files = Array.isArray(shareIntent.files) ? shareIntent.files : [];
  for (const f of files) {
    const mime = (f.mimeType ?? f.type ?? "").toLowerCase();
    if (mime === "application/pdf" || mime.startsWith("image/")) return f;
  }
  return null;
}

function deriveName(file: SharedFile, mime: string): string {
  if (file.fileName) return file.fileName;
  const path = file.path ?? file.uri ?? "";
  const tail = path.split("/").pop();
  if (tail && tail.includes(".")) return tail;
  const ext = mime === "application/pdf" ? "pdf" : mime.split("/")[1] || "bin";
  return `upload.${ext}`;
}

export type IncomingShareCallback = (result: {
  ok: boolean;
  message: string;
}) => void;

/**
 * Listens for shared content from the iOS share sheet or Android
 * ACTION_SEND intent. PDFs and images are routed to /ingest/file for
 * vision-based analysis; everything else falls through to URL ingest.
 * Network failures on URL ingest fall back to the offline queue.
 */
export function useIncomingShare(onResult: IncomingShareCallback): void {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntentContext();
  const lastProcessedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!hasShareIntent || !shareIntent) {
      lastProcessedRef.current = null;
      return;
    }

    const firstFile = Array.isArray(shareIntent.files)
      ? (shareIntent.files[0] as SharedFile | undefined)
      : undefined;
    const intentKey =
      `${shareIntent.webUrl ?? ""}|${shareIntent.text ?? ""}|` +
      `${firstFile?.path ?? firstFile?.uri ?? ""}`;
    if (lastProcessedRef.current === intentKey) return;
    lastProcessedRef.current = intentKey;

    const text: string =
      shareIntent.text ??
      shareIntent.webUrl ??
      "";

    // Files first — a PDF/image share usually has no URL, and even when it
    // does the file is what the user wants saved.
    const file = pickFile(shareIntent as { files?: SharedFile[] | null });
    if (file) {
      const mime = (file.mimeType ?? file.type ?? "").toLowerCase();
      const uri = file.uri ?? file.path ?? "";
      const name = deriveName(file, mime);
      const noteFromText = text && !URL_RE.test(text) ? text.trim() || null : null;

      api
        .ingestFile({ uri, name, mimeType: mime }, noteFromText ?? undefined)
        .then(() => {
          onResult({
            ok: true,
            message: `Saved ${mime === "application/pdf" ? "PDF" : "image"}. Processing in the background.`,
          });
        })
        .catch((err) => {
          const msg =
            err instanceof ApiError
              ? `Failed (${err.status}): ${err.message}`
              : `Upload failed: ${(err as Error).message}`;
          onResult({ ok: false, message: msg });
        })
        .finally(() => {
          resetShareIntent();
        });
      return;
    }

    const urlMatch = text.match(URL_RE);
    const url = shareIntent.webUrl ?? urlMatch?.[0];

    if (!url) {
      onResult({ ok: false, message: "Couldn't find a URL or file in the share." });
      resetShareIntent();
      return;
    }

    const shareText =
      text && text !== url ? text.replace(URL_RE, "").trim() || null : null;

    api
      .ingest(url, shareText ?? undefined)
      .then(async () => {
        // Drain anything queued while we were offline — best-effort.
        const flushed = await shareQueue.flush();
        const extra =
          flushed.sent > 0
            ? ` (+${flushed.sent} pending sent)`
            : "";
        onResult({
          ok: true,
          message: `Saved. Processing in the background.${extra}`,
        });
      })
      .catch(async (err) => {
        if (err instanceof ApiError) {
          // Server reachable but said no — don't queue, the same payload will
          // just fail again.
          onResult({
            ok: false,
            message: `Failed (${err.status}): ${err.message}`,
          });
          return;
        }
        // Network error / fetch threw — stash it for later.
        await shareQueue.enqueue({ url, shareText });
        onResult({
          ok: true,
          message: "Offline — saved locally and will retry when online.",
        });
      })
      .finally(() => {
        resetShareIntent();
      });
  }, [hasShareIntent, shareIntent, resetShareIntent, onResult]);
}
