import type { Ionicons } from "@expo/vector-icons";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

/** "https://www.karpathy.github.io/2019/..." → "karpathy.github.io" */
export function domainOf(url: string): string {
  const m = url.match(/^[a-z]+:\/\/([^/?#]+)/i);
  const host = m?.[1] ?? url;
  return host.replace(/^www\./i, "");
}

/** Path part of a URL, for rows whose only identity is the link itself. */
export function pathOf(url: string): string {
  const m = url.match(/^[a-z]+:\/\/[^/?#]+([^?#]*)/i);
  const path = m?.[1] ?? "";
  return path && path !== "/" ? path : "";
}

const SOURCE_KINDS: Record<string, { label: string; icon: IoniconName }> = {
  youtube: { label: "Video", icon: "play" },
  twitter: { label: "Post", icon: "logo-x" },
  instagram: { label: "Post", icon: "logo-instagram" },
  reddit: { label: "Post", icon: "logo-reddit" },
  pdf: { label: "PDF", icon: "document-text-outline" },
  image: { label: "Image", icon: "image-outline" },
};

export function sourceKind(source: string): { label: string; icon: IoniconName } {
  return SOURCE_KINDS[source] ?? { label: "Article", icon: "globe-outline" };
}

/**
 * "karpathy.github.io · Article". Shared files are stored as `file://<name>`
 * with the name as their title, so they get just the kind ("PDF", "Image").
 */
export function sourceMeta(url: string, source: string): string {
  const kind = sourceKind(source).label;
  return url.startsWith("file://") ? kind : `${domainOf(url)} · ${kind}`;
}

export function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${n} ${n === 1 ? singular : pluralForm}`;
}

const DAY_MS = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function daysAgo(iso: string, now = new Date()): number {
  return Math.max(0, Math.round((startOfDay(now) - startOfDay(new Date(iso))) / DAY_MS));
}

/** Short relative label: "just now", "3h ago", "yesterday", "5 days ago", "Apr 25". */
export function relativeTime(iso: string, now = new Date()): string {
  const then = new Date(iso);
  const diffMin = Math.floor((now.getTime() - then.getTime()) / 60_000);
  const days = daysAgo(iso, now);
  if (days === 0) {
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    return `${Math.floor(diffMin / 60)}h ago`;
  }
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  const sameYear = then.getFullYear() === now.getFullYear();
  return `${MONTHS[then.getMonth()]} ${then.getDate()}${sameYear ? "" : `, ${then.getFullYear()}`}`;
}

const ACRONYMS = new Map(
  [
    "AI", "LLM", "LLMs", "ML", "NLP", "API", "APIs", "GPU", "GPUs", "CPU", "RAG", "UI",
    "UX", "SQL", "HTTP", "CSS", "HTML", "AWS", "GPT", "RL", "RLHF", "PDF", "URL", "CEO",
    "iOS", "macOS", "JS", "TS", "YC", "VC",
  ].map((a) => [a.toLowerCase(), a]),
);

function fixWord(word: string): string {
  return ACRONYMS.get(word.toLowerCase()) ?? word;
}

/** Topic titles come from slugs on older pages ("Llm Scaling"); fix acronym casing. */
export function topicTitle(titleOrSlug: string): string {
  const words = titleOrSlug.includes(" ") ? titleOrSlug.split(" ") : titleOrSlug.split("-");
  return words
    .filter(Boolean)
    .map((w) => {
      const fixed = fixWord(w);
      if (fixed !== w) return fixed;
      return w.charAt(0).toUpperCase() + w.slice(1);
    })
    .join(" ");
}

/** Tags are kebab-case slugs: "neural-network-training" → "Neural network training". */
export function humanizeTag(tag: string): string {
  const words = tag.split("-").filter(Boolean).map(fixWord);
  if (words.length === 0) return tag;
  const [first, ...rest] = words;
  return [first!.charAt(0).toUpperCase() + first!.slice(1), ...rest].join(" ");
}

export function initials(nameOrEmail: string): string {
  const base = nameOrEmail.split("@")[0] ?? nameOrEmail;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.length >= 2 ? parts[0]![0]! + parts[1]![0]! : base.slice(0, 2);
  return letters.toUpperCase();
}

/** Shared PDFs/images are stored as `file://<name>` and deleted once processed. */
export function isUploadedFile(url: string): boolean {
  return url.startsWith("file://");
}

/** The worker stores exception text; show people what happened instead. */
export function friendlyIngestError(raw: string | null | undefined, url = ""): string {
  if (isUploadedFile(url)) return "Couldn’t process this file";
  const e = raw ?? "";
  if (/name or service not known|nodename nor servname|getaddrinfo|connecterror|connection refused/i.test(e)) {
    return "Couldn’t reach this site";
  }
  if (/timed? ?out|timeout/i.test(e)) return "The site took too long to respond";
  if (/\b(401|403)\b|forbidden|unauthori[sz]ed/i.test(e)) return "The site blocked access";
  if (/\b404\b|not found/i.test(e)) return "Page not found";
  if (/credit balance/i.test(e)) return "AI provider is out of credits";
  return "Couldn’t save this link";
}

export function friendlyAskError(raw: string): { title: string; detail: string } {
  if (/credit balance/i.test(raw)) {
    return {
      title: "Ask is unavailable right now",
      detail: "Your server’s AI provider rejected the request: the account is out of credits.",
    };
  }
  if (/network error|server url not configured/i.test(raw)) {
    return { title: "Can’t reach your server", detail: "Check the server address in Settings." };
  }
  if (/\b429\b|rate limit/i.test(raw)) {
    return { title: "Hourly limit reached", detail: "Try again in a little while." };
  }
  if (/session expired|not signed in|\b401\b/i.test(raw)) {
    return { title: "You’ve been signed out", detail: "Sign in again to keep asking." };
  }
  return { title: "Couldn’t get an answer", detail: "Something went wrong on the server. Try again." };
}
