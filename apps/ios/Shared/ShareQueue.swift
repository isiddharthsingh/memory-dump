import Foundation

/// A share on its way to the server. Lives in the App Group as
/// ShareQueue/<id>.json, with a shared file's bytes beside it as <id>.bin.
struct QueuedShare: Codable, Identifiable {
  let id: UUID
  let queuedAt: Date
  let note: String?
  let kind: Kind
  /// The page title or file name, for the Library while it waits.
  var title: String?

  enum Kind: Codable {
    case link(URL)
    case file(name: String, mimeType: String)
  }
}

/// Every share goes into the queue before it is sent, so closing the
/// extension or a server that never answers can't lose it. The extension
/// sends it at once; whatever the server didn't take, the app sends while it
/// is open, and from Settings.
enum ShareQueue {
  /// What one send did with its entry.
  enum Outcome {
    case sent
    /// Still queued: the server couldn't be reached, or the session needs a sign-in.
    case waiting(Error)
    /// Dropped: the server refused it, and would again.
    case refused(Error)
  }

  /// The other process (app or extension) is sending this entry right now.
  struct InProgress: Error {}

  private static var directory: URL { AppGroup.directory("ShareQueue") }
  private static var isFlushing = false
  /// No flush before this: the Retry-After of the last 429 or 503.
  private(set) static var notBefore: Date?

  static func enqueueLink(_ url: URL, note: String?, title: String?) throws -> QueuedShare {
    let entry = QueuedShare(id: UUID(), queuedAt: .now, note: note, kind: .link(url), title: title)
    try write(entry)
    return entry
  }

  /// Moves the file into the queue.
  static func enqueueFile(at file: URL, name: String, mimeType: String, note: String?) throws
    -> QueuedShare
  {
    let entry = QueuedShare(
      id: UUID(), queuedAt: .now, note: note, kind: .file(name: name, mimeType: mimeType), title: name)
    try FileManager.default.moveItem(at: file, to: bytesURL(entry.id))
    try write(entry)
    return entry
  }

  static func pending() -> [QueuedShare] {
    let files =
      (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil))
      ?? []
    return files.filter { $0.pathExtension == "json" }
      .compactMap { try? JSONDecoder().decode(QueuedShare.self, from: Data(contentsOf: $0)) }
      .sorted { $0.queuedAt < $1.queuedAt }
  }

  static var count: Int { pending().count }

  static func remove(_ entry: QueuedShare) {
    try? FileManager.default.removeItem(at: jsonURL(entry.id))
    try? FileManager.default.removeItem(at: bytesURL(entry.id))
    try? FileManager.default.removeItem(at: lockURL(entry.id))
  }

  static func clear() {
    pending().forEach(remove)
  }

  /// Sends one entry, and takes it off the queue unless it has to wait. One
  /// sender at a time across the app and the extension: an entry another one
  /// is sending waits, so the server never gets it twice.
  static func send(_ entry: QueuedShare, using api: APIClient = .shared) async -> Outcome {
    let lock = open(lockURL(entry.id).path(percentEncoded: false), O_CREAT | O_RDWR, 0o600)
    if lock >= 0, flock(lock, LOCK_EX | LOCK_NB) != 0 {
      close(lock)
      return .waiting(InProgress())
    }
    // Closing the file releases the lock, and so does the process ending.
    defer { if lock >= 0 { close(lock) } }
    // The other process sent it after this one listed the queue.
    guard FileManager.default.fileExists(atPath: jsonURL(entry.id).path(percentEncoded: false)) else {
      try? FileManager.default.removeItem(at: lockURL(entry.id))
      return .waiting(InProgress())
    }
    do {
      switch entry.kind {
      case .link(let url):
        _ = try await api.ingest(url, note: entry.note)
      case .file(let name, let mimeType):
        // The entry's id, so a resend after a timeout gets the item the
        // server already made. A link needs none: the server dedupes on URL.
        _ = try await api.ingestFile(
          at: bytesURL(entry.id), name: name, mimeType: mimeType, note: entry.note,
          idempotencyKey: entry.id.lower)
      }
      remove(entry)
      return .sent
    } catch {
      if waits(after: error) { return .waiting(error) }
      remove(entry)
      return .refused(error)
    }
  }

  /// Sends the queue oldest first. Stops when the server can't be reached or
  /// won't take more, since every share after would wait too; one that waits
  /// for its own reason (a 500, another sender) doesn't hold up the rest.
  /// Sends nothing until the server's Retry-After has passed.
  static func flush(using api: APIClient = .shared) async -> (sent: Int, refused: Int) {
    guard !isFlushing, (notBefore ?? .distantPast) <= .now else { return (0, 0) }
    isFlushing = true
    defer { isFlushing = false }
    var sent = 0
    var refused = 0
    for entry in pending() {
      if Task.isCancelled { break }
      switch await send(entry, using: api) {
      case .sent: sent += 1
      case .refused: refused += 1
      case .waiting(let error):
        if let seconds = error.retryAfter { notBefore = .now + seconds }
        if holdsUpQueue(error) { return (sent, refused) }
      }
    }
    return (sent, refused)
  }

  /// A share waits when sending it again later can work: no answer, a
  /// cancelled send, a server that is down, busy or rate-limiting, a session
  /// that needs a sign-in, or a build the server wants updated. A 4xx
  /// refusal would only repeat.
  static func waits(after error: Error) -> Bool {
    if error is CancellationError || error is URLError || error is InProgress { return true }
    switch error as? APIError {
    case .signedOut?, .updateRequired?: return true
    case .http(let status, _, _)?: return status == 408 || status == 429 || status >= 500
    default: return false
    }
  }

  /// Whether every share would wait after this: no server, no session, a
  /// rate limit, a build too old.
  static func holdsUpQueue(_ error: Error) -> Bool {
    switch error as? APIError {
    case .signedOut?, .updateRequired?: return true
    case .http(let status, _, _)?: return status == 408 || status == 429 || error.isUnreachable
    default: return error.isUnreachable || error is CancellationError
    }
  }

  private static func write(_ entry: QueuedShare) throws {
    try JSONEncoder().encode(entry).write(to: jsonURL(entry.id), options: .atomic)
  }

  private static func jsonURL(_ id: UUID) -> URL { directory.appending(path: "\(id.lower).json") }
  private static func bytesURL(_ id: UUID) -> URL { directory.appending(path: "\(id.lower).bin") }
  private static func lockURL(_ id: UUID) -> URL { directory.appending(path: "\(id.lower).lock") }
}

/// How a share's text becomes a URL and a note. The same rules as the Expo
/// app's useIncomingShare.
enum ShareText {
  static func firstHTTPURL(in text: String) -> URL? {
    text.firstMatch(of: /https?:\/\/\S+/.ignoresCase()).flatMap { URL(string: String($0.output)) }
  }

  /// For a file share: the text is a note only when it holds no URL.
  static func fileNote(from text: String?) -> String? {
    guard let text, firstHTTPURL(in: text) == nil else { return nil }
    return text.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
  }

  /// For a link share: the text without its first URL. Nil when the text is
  /// only the URL.
  static func linkNote(from text: String?, url: URL) -> String? {
    guard let text, text != url.absoluteString else { return nil }
    var rest = text
    if let match = text.firstMatch(of: /https?:\/\/\S+/.ignoresCase()) {
      rest.removeSubrange(match.range)
    }
    return rest.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
  }
}

extension String {
  var nilIfEmpty: String? { isEmpty ? nil : self }
}
