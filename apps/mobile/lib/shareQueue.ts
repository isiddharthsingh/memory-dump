import * as SecureStore from "expo-secure-store";

import { api, ApiError } from "./api";

const QUEUE_KEY = "memory_dump_share_queue";

export type QueuedShare = {
  url: string;
  shareText: string | null;
  queuedAt: number;
};

/**
 * Persistent FIFO queue for shares that couldn't reach the server.
 *
 * Storage: a single SecureStore key holds the whole queue as JSON. SecureStore
 * values are capped at ~2KB on iOS — for our payload (URL + short note) that's
 * roughly 10-20 entries. If a write would exceed that, we trim the oldest.
 *
 * We deliberately don't pull in AsyncStorage / expo-file-system here because
 * both require a native rebuild and the queue is small by nature.
 */

const MAX_BYTES = 1800; // leave headroom under SecureStore's ~2KB cap

async function readQueue(): Promise<QueuedShare[]> {
  const raw = await SecureStore.getItemAsync(QUEUE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as QueuedShare[]) : [];
  } catch {
    return [];
  }
}

async function writeQueue(q: QueuedShare[]): Promise<void> {
  if (q.length === 0) {
    await SecureStore.deleteItemAsync(QUEUE_KEY);
    return;
  }
  // Trim oldest entries until we fit under the size cap. Newer shares are
  // more relevant — losing the oldest is the right failure mode.
  let serialized = JSON.stringify(q);
  while (serialized.length > MAX_BYTES && q.length > 1) {
    q.shift();
    serialized = JSON.stringify(q);
  }
  await SecureStore.setItemAsync(QUEUE_KEY, serialized);
}

export const shareQueue = {
  async enqueue(entry: { url: string; shareText: string | null }): Promise<void> {
    const q = await readQueue();
    q.push({ url: entry.url, shareText: entry.shareText, queuedAt: Date.now() });
    await writeQueue(q);
  },

  async count(): Promise<number> {
    const q = await readQueue();
    return q.length;
  },

  async list(): Promise<QueuedShare[]> {
    return readQueue();
  },

  async clear(): Promise<void> {
    await SecureStore.deleteItemAsync(QUEUE_KEY);
  },

  /**
   * Try to replay the queue one entry at a time. Stops at the first network
   * failure (so we don't burn through every pending share when the network
   * is still down). 4xx/5xx server errors are treated as permanent — the
   * entry is dropped and an error counter incremented in the result.
   *
   * Returns counts so the caller can show a toast.
   */
  async flush(): Promise<{ sent: number; failed: number; remaining: number }> {
    let q = await readQueue();
    let sent = 0;
    let failed = 0;
    while (q.length > 0) {
      const next = q[0]!;
      try {
        await api.ingest(next.url, next.shareText ?? undefined);
        sent += 1;
        q = q.slice(1);
        await writeQueue(q);
      } catch (err) {
        if (err instanceof ApiError) {
          // The server reachable but rejected this entry — drop it and move on.
          failed += 1;
          q = q.slice(1);
          await writeQueue(q);
          continue;
        }
        // Network error (TypeError "Network request failed" etc.) — bail.
        break;
      }
    }
    return { sent, failed, remaining: q.length };
  },
};
