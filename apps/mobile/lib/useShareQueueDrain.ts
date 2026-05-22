import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { AppState, AppStateStatus } from "react-native";

import { shareQueue } from "./shareQueue";

/**
 * Attempts to flush the offline share queue whenever the app transitions
 * to active. Also runs once on mount. On any send, the Inbox list query is
 * invalidated so the new items appear without a manual pull-to-refresh.
 *
 * Callback fires with `sent` count when one or more shares went through, so
 * the layout can surface a toast.
 */
export function useShareQueueDrain(
  onFlushed?: (sent: number) => void,
): void {
  const qc = useQueryClient();
  const onFlushedRef = useRef(onFlushed);
  onFlushedRef.current = onFlushed;

  useEffect(() => {
    let cancelled = false;

    const tryFlush = async () => {
      const count = await shareQueue.count();
      if (count === 0) return;
      const result = await shareQueue.flush();
      if (cancelled) return;
      if (result.sent > 0) {
        qc.invalidateQueries({ queryKey: ["items"] });
        onFlushedRef.current?.(result.sent);
      }
    };

    void tryFlush();

    const sub = AppState.addEventListener("change", (status: AppStateStatus) => {
      if (status === "active") void tryFlush();
    });
    return () => {
      cancelled = true;
      sub.remove();
    };
  }, [qc]);
}
