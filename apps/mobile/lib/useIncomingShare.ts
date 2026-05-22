import { useShareIntentContext } from "expo-share-intent";
import { useEffect, useRef } from "react";

import { api, ApiError } from "./api";
import { shareQueue } from "./shareQueue";

const URL_RE = /https?:\/\/\S+/i;

export type IncomingShareCallback = (result: {
  ok: boolean;
  message: string;
}) => void;

/**
 * Listens for shared text/URLs from the iOS share sheet or Android
 * ACTION_SEND intent and POSTs them to /ingest. Network failures fall back
 * to the offline queue so the share isn't lost.
 */
export function useIncomingShare(onResult: IncomingShareCallback): void {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntentContext();
  const lastProcessedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!hasShareIntent || !shareIntent) {
      lastProcessedRef.current = null;
      return;
    }

    const intentKey = `${shareIntent.webUrl ?? ""}|${shareIntent.text ?? ""}`;
    if (lastProcessedRef.current === intentKey) return;
    lastProcessedRef.current = intentKey;

    const text: string =
      shareIntent.text ??
      shareIntent.webUrl ??
      (Array.isArray(shareIntent.files) ? shareIntent.files[0]?.path : undefined) ??
      "";

    const urlMatch = text.match(URL_RE);
    const url = shareIntent.webUrl ?? urlMatch?.[0];

    if (!url) {
      onResult({ ok: false, message: "Couldn't find a URL in the shared text." });
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
