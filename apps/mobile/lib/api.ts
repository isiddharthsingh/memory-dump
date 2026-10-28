import { z } from "zod";

import { Session, auth, clearSession, loadSession } from "./auth";
import { loadConfig } from "./settings";

const ItemSummary = z.object({
  id: z.string().uuid(),
  url: z.string(),
  source: z.string(),
  status: z.string(),
  title: z.string().nullable(),
  summary: z.string().nullable(),
  thumbnail_url: z.string().nullable(),
  tags: z.array(z.string()).nullable(),
  // Optional so the app still parses servers older than this field.
  error: z.string().nullable().optional(),
  created_at: z.string(),
});

const ItemDetail = ItemSummary.extend({
  raw_text: z.string().nullable(),
  people: z.array(z.string()).nullable(),
  handles: z.array(z.string()).nullable(),
  brands_products: z.array(z.string()).nullable(),
  topics: z.array(z.string()).nullable(),
  share_text: z.string().nullable(),
});

const TopicPageSummary = z.object({
  id: z.string().uuid(),
  slug: z.string(),
  title: z.string(),
  backlinks: z.array(z.string()).nullable(),
  source_item_ids: z.array(z.string().uuid()).nullable(),
  version: z.number(),
  updated_at: z.string(),
});

const TopicPageDetail = TopicPageSummary.extend({
  body_md: z.string(),
});

const SearchHit = z.object({
  kind: z.enum(["item", "topic_page"]),
  id: z.string().uuid(),
  title: z.string().nullable(),
  url: z.string().nullable().optional(),
  slug: z.string().nullable().optional(),
  snippet: z.string(),
  score: z.number(),
});

const Citation = z.object({
  kind: z.enum(["item", "topic_page"]),
  id: z.string().uuid(),
  title: z.string().nullable(),
  url: z.string().nullable().optional(),
  slug: z.string().nullable().optional(),
});

const TagCount = z.object({ tag: z.string(), count: z.number() });

export type ItemSummary = z.infer<typeof ItemSummary>;
export type ItemDetail = z.infer<typeof ItemDetail>;
export type TopicPageSummary = z.infer<typeof TopicPageSummary>;
export type TopicPageDetail = z.infer<typeof TopicPageDetail>;
export type SearchHit = z.infer<typeof SearchHit>;
export type Citation = z.infer<typeof Citation>;
export type TagCount = z.infer<typeof TagCount>;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Hook for AuthContext to register a callback that fires when refresh
 * fails and the user must be signed out. Keeps api.ts free of React.
 */
let onSessionInvalid: (() => void) | null = null;
export function setOnSessionInvalid(cb: (() => void) | null): void {
  onSessionInvalid = cb;
}

// Coalesce concurrent refresh attempts so a burst of 401s doesn't fire
// N refresh calls.
let refreshInFlight: Promise<Session | null> | null = null;

async function refreshSession(currentRefresh: string): Promise<Session | null> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      return await auth.refresh(currentRefresh);
    } catch {
      await clearSession();
      onSessionInvalid?.();
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function authedFetch(
  path: string,
  init: RequestInit,
): Promise<Response> {
  const cfg = await loadConfig();
  if (!cfg.serverUrl) {
    throw new ApiError(0, "Server URL not configured.");
  }
  let session = await loadSession();
  if (!session) {
    throw new ApiError(401, "Not signed in.");
  }
  const url = cfg.serverUrl.replace(/\/$/, "") + path;

  const send = async (token: string) =>
    fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers || {}),
        Authorization: `Bearer ${token}`,
      },
    });

  let resp = await send(session.accessToken);
  if (resp.status === 401) {
    const refreshed = await refreshSession(session.refreshToken);
    if (!refreshed) {
      throw new ApiError(401, "Session expired.");
    }
    session = refreshed;
    resp = await send(session.accessToken);
  }
  return resp;
}

async function request<T>(
  path: string,
  init: RequestInit,
  schema: z.ZodSchema<T>,
): Promise<T> {
  const resp = await authedFetch(path, init);
  if (!resp.ok) {
    let detail = "";
    try {
      const body = await resp.json();
      detail = body?.detail ?? "";
    } catch {
      detail = await resp.text().catch(() => "");
    }
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  const json = await resp.json();
  return schema.parse(json);
}

export const api = {
  listItems(params: {
    limit?: number;
    offset?: number;
    status?: string;
    tag?: string;
    source?: string;
  } = {}): Promise<ItemSummary[]> {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    if (params.status) qs.set("status", params.status);
    if (params.tag) qs.set("tag", params.tag);
    if (params.source) qs.set("source", params.source);
    const qstr = qs.toString();
    return request(
      `/items${qstr ? `?${qstr}` : ""}`,
      { method: "GET" },
      z.array(ItemSummary),
    );
  },

  getItem(id: string): Promise<ItemDetail> {
    return request(`/items/${id}`, { method: "GET" }, ItemDetail);
  },

  retryItem(id: string): Promise<ItemDetail> {
    return request(`/items/${id}/retry`, { method: "POST" }, ItemDetail);
  },

  updateItem(id: string, patch: { share_text?: string | null }): Promise<ItemDetail> {
    return request(
      `/items/${id}`,
      { method: "PATCH", body: JSON.stringify(patch) },
      ItemDetail,
    );
  },

  deleteItem(id: string): Promise<void> {
    return request(`/items/${id}`, { method: "DELETE" }, z.unknown()).then(
      () => undefined,
    );
  },

  ingest(url: string, shareText?: string): Promise<{ item: ItemSummary }> {
    return request(
      `/ingest`,
      {
        method: "POST",
        body: JSON.stringify({ url, share_text: shareText ?? null }),
      },
      z.object({ item: ItemSummary }),
    );
  },

  async ingestFile(
    file: { uri: string; name: string; mimeType: string },
    shareText?: string,
  ): Promise<{ item: ItemSummary }> {
    const cfg = await loadConfig();
    if (!cfg.serverUrl) {
      throw new ApiError(0, "Server URL not configured.");
    }
    let session = await loadSession();
    if (!session) throw new ApiError(401, "Not signed in.");

    const form = new FormData();
    // React Native's FormData accepts the { uri, name, type } shape; the
    // server reads it as a standard multipart/form-data upload.
    form.append("file", {
      uri: file.uri,
      name: file.name,
      type: file.mimeType,
    } as unknown as Blob);
    if (shareText) form.append("share_text", shareText);

    const url = cfg.serverUrl.replace(/\/$/, "") + "/ingest/file";
    const send = async (token: string) =>
      fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          // Do NOT set Content-Type — fetch sets the multipart boundary itself.
        },
        body: form as unknown as BodyInit,
      });

    let resp = await send(session.accessToken);
    if (resp.status === 401) {
      const refreshed = await refreshSession(session.refreshToken);
      if (!refreshed) throw new ApiError(401, "Session expired.");
      session = refreshed;
      resp = await send(session.accessToken);
    }
    if (!resp.ok) {
      let detail = "";
      try {
        const body = await resp.json();
        detail = body?.detail ?? "";
      } catch {
        detail = await resp.text().catch(() => "");
      }
      throw new ApiError(resp.status, detail || resp.statusText);
    }
    return z.object({ item: ItemSummary }).parse(await resp.json());
  },

  search(q: string, limit = 10): Promise<{ hits: SearchHit[] }> {
    return request(
      `/search`,
      { method: "POST", body: JSON.stringify({ q, limit }) },
      z.object({ hits: z.array(SearchHit) }),
    );
  },

  ask(question: string): Promise<{ answer: string; citations: Citation[] }> {
    return request(
      `/ask`,
      { method: "POST", body: JSON.stringify({ question }) },
      z.object({ answer: z.string(), citations: z.array(Citation) }),
    );
  },

  askStream(
    question: string,
    handlers: {
      onToken: (text: string) => void;
      onDone: (citations: Citation[]) => void;
      onError: (err: Error) => void;
    },
  ): { abort: () => void } {
    const xhr = new XMLHttpRequest();
    let consumed = 0;
    let cancelled = false;

    const processBuffer = (buf: string) => {
      // SSE frames are separated by a blank line.
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        consumed += frame.length + 2;
        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          const parsed = JSON.parse(data);
          if (event === "token") handlers.onToken(parsed.text ?? "");
          else if (event === "done") {
            const c = z.array(Citation).safeParse(parsed.citations ?? []);
            handlers.onDone(c.success ? c.data : []);
          } else if (event === "error") {
            handlers.onError(new Error(parsed.message ?? "stream error"));
          }
        } catch (e) {
          handlers.onError(e as Error);
        }
      }
    };

    (async () => {
      const cfg = await loadConfig();
      if (cancelled) return;
      if (!cfg.serverUrl) {
        handlers.onError(new ApiError(0, "Server URL not configured."));
        return;
      }
      let session = await loadSession();
      if (!session) {
        handlers.onError(new ApiError(401, "Not signed in."));
        return;
      }
      const url = cfg.serverUrl.replace(/\/$/, "") + "/ask/stream";

      const open = (token: string) => {
        xhr.open("POST", url);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.setRequestHeader("Accept", "text/event-stream");
        xhr.onreadystatechange = () => {
          if (xhr.readyState >= 3 && xhr.status >= 200 && xhr.status < 300) {
            const fresh = xhr.responseText.slice(consumed);
            if (fresh.includes("\n\n")) processBuffer(fresh);
          }
          if (xhr.readyState === 4) {
            if (xhr.status === 401) {
              // 401 mid-stream: try a single refresh + retry. Avoid
              // refreshing the same token from the stream while another
              // request might already be refreshing.
              refreshSession(session!.refreshToken).then((refreshed) => {
                if (!refreshed) {
                  handlers.onError(new ApiError(401, "Session expired."));
                  return;
                }
                session = refreshed;
                consumed = 0;
                open(session.accessToken);
                xhr.send(JSON.stringify({ question }));
              });
            } else if (xhr.status < 200 || xhr.status >= 300) {
              handlers.onError(new ApiError(xhr.status, xhr.responseText || xhr.statusText));
            }
          }
        };
        xhr.onerror = () => {
          if (!cancelled) handlers.onError(new ApiError(0, "Network error"));
        };
      };

      open(session.accessToken);
      xhr.send(JSON.stringify({ question }));
    })().catch((err) => handlers.onError(err as Error));

    return {
      abort: () => {
        cancelled = true;
        try {
          xhr.abort();
        } catch {
          // ignore
        }
      },
    };
  },

  listTags(): Promise<TagCount[]> {
    return request(
      `/items/tags`,
      { method: "GET" },
      z.object({ tags: z.array(TagCount) }),
    ).then((r) => r.tags);
  },

  listWiki(): Promise<TopicPageSummary[]> {
    return request(`/wiki`, { method: "GET" }, z.array(TopicPageSummary));
  },

  getWikiPage(slug: string): Promise<TopicPageDetail> {
    return request(`/wiki/${slug}`, { method: "GET" }, TopicPageDetail);
  },

  related(
    id: string,
  ): Promise<{ in_wiki: TopicPageSummary[]; related: ItemSummary[] }> {
    return request(
      `/items/${id}/related`,
      { method: "GET" },
      z.object({
        in_wiki: z.array(TopicPageSummary),
        related: z.array(ItemSummary),
      }),
    );
  },

  health(): Promise<{ status: string }> {
    // `/healthz` is unauthenticated — don't run it through authedFetch.
    return (async () => {
      const cfg = await loadConfig();
      if (!cfg.serverUrl) throw new ApiError(0, "Server URL not configured.");
      const url = cfg.serverUrl.replace(/\/$/, "") + "/healthz";
      const resp = await fetch(url, { method: "GET" });
      if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
      return z.object({ status: z.string() }).parse(await resp.json());
    })();
  },

  registerDevice(token: string, platform?: string): Promise<{ id: string }> {
    return request(
      `/devices/register`,
      {
        method: "POST",
        body: JSON.stringify({ expo_push_token: token, platform }),
      },
      z.object({ id: z.string() }),
    );
  },
};
