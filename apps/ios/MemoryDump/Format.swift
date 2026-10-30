import Foundation

/// Display helpers, ported from the Expo app's lib/format.ts.
enum Format {
  /// Subtitle for a list showing its saved copy because the server is out of reach.
  static let offline = "Offline · showing your saved copy"

  /// "https://www.karpathy.github.io/2019/..." → "karpathy.github.io". Uploaded
  /// files give their name: "file://<id>/report.pdf" → "report.pdf".
  static func domain(_ url: String) -> String {
    if isUploadedFile(url) { return url.split(separator: "/").last.map(String.init) ?? url }
    let host = url.firstMatch(of: /^[a-z]+:\/\/([^\/?#]+)/.ignoresCase()).map { String($0.1) } ?? url
    return host.replacing(/^www\./.ignoresCase(), with: "")
  }

  /// Path part of a URL, for rows whose only identity is the link itself.
  static func path(_ url: String) -> String {
    let path = url.firstMatch(of: /^[a-z]+:\/\/[^\/?#]+([^?#]*)/.ignoresCase()).map { String($0.1) } ?? ""
    return path == "/" ? "" : path
  }

  struct SourceKind {
    let label: String
    let symbol: String
  }

  static func sourceKind(_ source: String) -> SourceKind {
    switch source {
    case "youtube": SourceKind(label: "Video", symbol: "play.fill")
    case "twitter": SourceKind(label: "Post", symbol: "xmark")
    case "instagram": SourceKind(label: "Post", symbol: "camera")
    case "reddit": SourceKind(label: "Post", symbol: "bubble.left.and.bubble.right")
    case "pdf": SourceKind(label: "PDF", symbol: "doc.text")
    case "image": SourceKind(label: "Image", symbol: "photo")
    default: SourceKind(label: "Article", symbol: "globe")
    }
  }

  /// The source a link is filed under, from its host alone: "youtube",
  /// "twitter" or "web". For search hits and citations, which carry no source.
  static func source(ofURL url: String?) -> String {
    let host = url.map(domain) ?? ""
    if host.contains(/(^|\.)youtube\.com$|^youtu\.be$/) { return "youtube" }
    if host.contains(/(^|\.)(x|twitter)\.com$/) { return "twitter" }
    return "web"
  }

  /// FNV-1a over UTF-16 units. Stable across launches, unlike Hasher, so a
  /// pick or a color keyed on it stays put.
  static func fnv1a(_ s: String) -> UInt32 {
    var h: UInt32 = 0x811C_9DC5
    for unit in s.utf16 {
      h ^= UInt32(unit)
      h = h &* 0x0100_0193
    }
    return h
  }

  /// "karpathy.github.io · Article". Shared files are stored as `file://<id>/<name>`
  /// with the name as their title, so they get only the kind.
  static func sourceMeta(_ url: String, _ source: String) -> String {
    let kind = sourceKind(source).label
    return isUploadedFile(url) ? kind : "\(domain(url)) · \(kind)"
  }

  static func plural(_ n: Int, _ singular: String, _ pluralForm: String? = nil) -> String {
    "\(n) \(n == 1 ? singular : pluralForm ?? singular + "s")"
  }

  static func daysAgo(_ date: Date, now: Date = .now) -> Int {
    let cal = Calendar.current
    let days = cal.dateComponents([.day], from: cal.startOfDay(for: date), to: cal.startOfDay(for: now)).day ?? 0
    return max(0, days)
  }

  /// "just now", "3h ago", "yesterday", "5 days ago", "Apr 25".
  static func relativeTime(_ date: Date, now: Date = .now) -> String {
    let minutes = Int(now.timeIntervalSince(date) / 60)
    let days = daysAgo(date, now: now)
    if days == 0 {
      if minutes < 1 { return "just now" }
      if minutes < 60 { return "\(minutes)m ago" }
      return "\(minutes / 60)h ago"
    }
    if days == 1 { return "yesterday" }
    if days < 30 { return "\(days) days ago" }
    let sameYear = Calendar.current.isDate(date, equalTo: now, toGranularity: .year)
    return date.formatted(sameYear ? .dateTime.month(.abbreviated).day() : .dateTime.month(.abbreviated).day().year())
  }

  private static let acronyms: [String: String] = Dictionary(
    uniqueKeysWithValues: [
      "AI", "LLM", "LLMs", "ML", "NLP", "API", "APIs", "GPU", "GPUs", "CPU", "RAG", "UI",
      "UX", "SQL", "HTTP", "CSS", "HTML", "AWS", "GPT", "RL", "RLHF", "PDF", "URL", "CEO",
      "iOS", "macOS", "JS", "TS", "YC", "VC",
    ].map { ($0.lowercased(), $0) })

  private static func fixWord(_ word: String) -> String { acronyms[word.lowercased()] ?? word }

  /// Topic titles come from slugs on older pages ("Llm Scaling"); fix acronym casing.
  static func topicTitle(_ titleOrSlug: String) -> String {
    let words = titleOrSlug.contains(" ") ? titleOrSlug.split(separator: " ") : titleOrSlug.split(separator: "-")
    return words.map { w in
      let word = String(w)
      let fixed = fixWord(word)
      return fixed != word ? fixed : word.prefix(1).uppercased() + word.dropFirst()
    }.joined(separator: " ")
  }

  /// Tags are kebab-case slugs: "neural-network-training" → "Neural network training".
  static func humanizeTag(_ tag: String) -> String {
    let words = tag.split(separator: "-").map { fixWord(String($0)) }
    guard let first = words.first else { return tag }
    return ([first.prefix(1).uppercased() + first.dropFirst()] + words.dropFirst()).joined(separator: " ")
  }

  static func initials(_ nameOrEmail: String) -> String {
    let base = nameOrEmail.split(separator: "@").first.map(String.init) ?? nameOrEmail
    let parts = base.split(whereSeparator: { " ._-".contains($0) })
    let letters = parts.count >= 2 ? String(parts[0].prefix(1) + parts[1].prefix(1)) : String(base.prefix(2))
    return letters.uppercased()
  }

  /// Shared PDFs and images are stored as `file://<id>/<name>` and deleted once processed.
  static func isUploadedFile(_ url: String) -> Bool { url.hasPrefix("file://") }

  /// The worker stores exception text; show people what happened instead.
  static func friendlyIngestError(_ raw: String?, url: String = "") -> String {
    if isUploadedFile(url) { return "Couldn’t process this file" }
    let e = raw ?? ""
    // The server only fetches addresses on the open internet.
    if e.hasPrefix("NotPublic") { return "This link points to a private network" }
    if e.contains(/(?i)name or service not known|nodename nor servname|getaddrinfo|connecterror|connection refused/) {
      return "Couldn’t reach this site"
    }
    if e.contains(/(?i)timed? ?out|timeout/) { return "The site took too long to respond" }
    if e.contains(/(?i)\b(401|403)\b|forbidden|unauthori[sz]ed/) { return "The site blocked access" }
    if e.contains(/(?i)\b404\b|not found/) { return "Page not found" }
    if e.contains(/(?i)credit balance/) { return "AI provider is out of credits" }
    return "Couldn’t save this link"
  }

  static func friendlyAskError(_ error: Error) -> (title: String, detail: String) {
    let raw = error.localizedDescription
    if raw.contains(/(?i)credit balance/) {
      return ("Ask is unavailable right now", "Your server’s AI provider rejected the request: the account is out of credits.")
    }
    if error is URLError {
      return ("Can’t reach your server", "Check your connection and try again.")
    }
    if case APIError.http(429, _) = error {
      return ("Hourly limit reached", "Try again in a little while.")
    }
    if (error as? APIError) == .signedOut {
      return ("You’ve been signed out", "Sign in again to keep asking.")
    }
    return ("Couldn’t get an answer", "Something went wrong on the server. Try again.")
  }
}

/// Picks the readable part of `raw_text`, which extractors write as sections
/// like "Title: …", "Body:\n…", "Transcript:\n…", "User note:\n…".
/// Ported from the Expo app's lib/readerContent.ts.
struct ReaderContent: Equatable {
  let body: String?
  /// The section the body came from: "Body", "Transcript", "Tweet"…
  let bodySection: String?
  /// A post's own text. Its body is the article it links to, when there is one.
  let post: String?
  let note: String?
  /// The video's channel or the post's author.
  let author: String?
  let isStub: Bool

  private static let bodyPriority = ["Body", "Transcript", "Tweet", "Description", "Summary"]

  init(rawText: String?) {
    guard let rawText, !rawText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      self.init(body: nil, bodySection: nil, post: nil, note: nil, author: nil, isStub: true)
      return
    }
    var sections: [String: String] = [:]
    for chunk in rawText.split(separator: /\n\n+/) {
      let trimmed = chunk.trimmingCharacters(in: .whitespacesAndNewlines)
      guard let m = trimmed.wholeMatch(of: /(?s)([A-Za-z][A-Za-z ]+?):\s*(.*)/) else { continue }
      let key = m.1.trimmingCharacters(in: .whitespaces)
      if sections[key] == nil { sections[key] = m.2.trimmingCharacters(in: .whitespacesAndNewlines) }
    }
    let section = Self.bodyPriority.first { sections[$0]?.isEmpty == false }
    let body = section.flatMap { sections[$0] }
    // A post that links to a video carries the video's Channel too, after its own Author.
    self.init(
      body: body, bodySection: section, post: sections["Tweet"], note: sections["User note"],
      author: sections["Author"] ?? sections["Channel"], isStub: (body?.count ?? 0) < 40)
  }

  private init(
    body: String?, bodySection: String?, post: String?, note: String?, author: String?, isStub: Bool
  ) {
    self.body = body
    self.bodySection = bodySection
    self.post = post
    self.note = note
    self.author = author
    self.isStub = isStub
  }
}
