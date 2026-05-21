import { useShareIntent } from "expo-share-intent";
import { useEffect } from "react";

import { api, ApiError } from "./api";

const URL_RE = /https?:\/\/\S+/i;

export type IncomingShareCallback = (result: {
  ok: boolean;
  message: string;
}) => void;

/**
 * Listens for shared text/URLs from the iOS share sheet or Android
 * ACTION_SEND intent and POSTs them to /ingest.
 */
export function useIncomingShare(onResult: IncomingShareCallback): void {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent();

  useEffect(() => {
    if (!hasShareIntent || !shareIntent) return;

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
      .then(() => {
        onResult({ ok: true, message: "Saved. Processing in the background." });
      })
      .catch((err) => {
        const msg =
          err instanceof ApiError
            ? `Failed (${err.status}): ${err.message}`
            : "Failed to save share.";
        onResult({ ok: false, message: msg });
      })
      .finally(() => {
        resetShareIntent();
      });
  }, [hasShareIntent, shareIntent, resetShareIntent, onResult]);
}
