import Foundation

/// A share that could not reach the server. Lives in the App Group as
/// ShareQueue/<id>.json, with a shared file's bytes beside it as <id>.bin.
struct QueuedShare: Codable, Identifiable {
  let id: UUID
  let queuedAt: Date
  let note: String?
  let kind: Kind

  enum Kind: Codable {
    case link(URL)
    case file(name: String, mimeType: String)
  }
}

/// The extension adds to the queue when it is offline; the app sends the queue
/// when it becomes active, and from Settings.
enum ShareQueue {
  private static var directory: URL { AppGroup.directory("ShareQueue") }
  private static var isFlushing = false

  static func enqueueLink(_ url: URL, note: String?) throws {
    try write(QueuedShare(id: UUID(), queuedAt: .now, note: note, kind: .link(url)))
  }

  /// Moves the file into the queue.
  static func enqueueFile(at file: URL, name: String, mimeType: String, note: String?) throws {
    let entry = QueuedShare(
      id: UUID(), queuedAt: .now, note: note, kind: .file(name: name, mimeType: mimeType))
    try FileManager.default.moveItem(at: file, to: bytesURL(entry.id))
    try write(entry)
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
  }

  static func clear() {
    pending().forEach(remove)
  }

  /// Sends the queue oldest first. Stops at the first network error, so a
  /// share is never lost while the network is still down. Drops an entry the
  /// server refuses, because the same request would fail again.
  static func flush(using api: APIClient = .shared) async -> (sent: Int, refused: Int) {
    guard !isFlushing else { return (0, 0) }
    isFlushing = true
    defer { isFlushing = false }
    var sent = 0
    var refused = 0
    for entry in pending() {
      do {
        switch entry.kind {
        case .link(let url):
          _ = try await api.ingest(url, note: entry.note)
        case .file(let name, let mimeType):
          _ = try await api.ingestFile(
            at: bytesURL(entry.id), name: name, mimeType: mimeType, note: entry.note)
        }
        sent += 1
      } catch APIError.http {
        refused += 1
      } catch {
        break
      }
      remove(entry)
    }
    return (sent, refused)
  }

  private static func write(_ entry: QueuedShare) throws {
    try JSONEncoder().encode(entry).write(to: jsonURL(entry.id), options: .atomic)
  }

  private static func jsonURL(_ id: UUID) -> URL { directory.appending(path: "\(id.lower).json") }
  private static func bytesURL(_ id: UUID) -> URL { directory.appending(path: "\(id.lower).bin") }
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
