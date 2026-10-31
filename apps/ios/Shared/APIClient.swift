import Foundation
import Observation

/// The backend API, under /v1. Every authorized call adds the access token,
/// and after a 401 refreshes it once (see SessionStore.refresh) and tries again.
///
/// Every GET's answer is saved for the signed-in account (SavedResponses).
/// When the server can't be reached, a GET returns that saved copy instead.
/// A copy saved with an ETag is sent as If-None-Match, and a 304 returns it.
@Observable
final class APIClient {
  static let shared = APIClient()
  /// Reads only the saved copies, never the network, so a screen can show
  /// them before the server answers. Throws APIError.notSaved when there's none.
  static let saved = APIClient(savedOnly: true)

  /// No URLCache: SavedResponses is the cache, and URLSession's own would
  /// answer a conditional GET's 304 with its copy instead of passing it on.
  static let session: URLSession = {
    let config = URLSessionConfiguration.default
    config.urlCache = nil
    config.requestCachePolicy = .reloadIgnoringLocalCacheData
    return URLSession(configuration: config)
  }()

  /// This build's CFBundleVersion, sent as X-App-Build with every request.
  static let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"

  /// The last request couldn't reach the server, so reads are saved copies.
  private(set) var isOffline = false
  /// What a 426 said: the server no longer takes this build. Every request
  /// fails with it, without going out, until `checkBuildAgain()`.
  private(set) var updateRequired: String?
  /// Called once when a 426 sets `updateRequired`. The app asks for an update.
  var onUpdateRequired: ((String) -> Void)?

  private let savedOnly: Bool
  private let saves = SavedResponses.shared
  private let store = SessionStore.shared
  private let http = APIClient.session
  private let decoder = JSONDecoder.api

  private init(savedOnly: Bool = false) {
    self.savedOnly = savedOnly
  }

  // MARK: Items

  /// Newest first. `before` is the last item of the previous page.
  func listItems(
    limit: Int? = nil, before: UUID? = nil, status: String? = nil, tag: String? = nil,
    source: String? = nil
  ) async throws -> [ItemSummary] {
    let query: [String: String?] = [
      "limit": limit.map(String.init), "before": before?.lower, "status": status,
      "tag": tag, "source": source,
    ]
    return try await send("/items", query: query)
  }

  /// What changed since the `until` of the last call; nil `since` the first time.
  /// Not saved: each answer is good for one sync.
  func itemChanges(since: String?) async throws -> ItemChanges {
    try await send("/items/changes", query: ["since": since], saves: false)
  }

  func item(_ id: UUID) async throws -> ItemDetail {
    try await send("/items/\(id.lower)")
  }

  func retryItem(_ id: UUID) async throws -> ItemDetail {
    try await send("/items/\(id.lower)/retry", method: "POST", saveAs: "/items/\(id.lower)")
  }

  func updateNote(_ id: UUID, note: String?) async throws -> ItemDetail {
    try await send(
      "/items/\(id.lower)", method: "PATCH", body: ["share_text": note], saveAs: "/items/\(id.lower)")
  }

  func deleteItem(_ id: UUID) async throws {
    _ = try await data("/items/\(id.lower)", method: "DELETE")
    forget("/items/\(id.lower)")
  }

  func related(_ id: UUID) async throws -> RelatedResponse {
    try await send("/items/\(id.lower)/related")
  }

  func tags() async throws -> [TagCount] {
    let r: TagsResponse = try await send("/items/tags")
    return r.tags
  }

  /// The thumbnail the server keeps for an uploaded image or PDF, at its
  /// `thumbnail_url`. Not saved here: ImageCache keeps it with the others.
  func thumbnail(_ path: String) async throws -> (Data, HTTPURLResponse) {
    try await authorized { token in
      try self.request(path, token: token)
    } perform: { req in
      try await self.http.data(for: req)
    }
  }

  // MARK: Ingest

  /// A share waits in the queue when the server doesn't answer, so it gives up
  /// as soon as a read does rather than after the default minute. For an
  /// upload this is the longest pause between bytes, not the whole upload.
  private static let shareTimeout: TimeInterval = 10

  func ingest(_ url: URL, note: String?) async throws -> ItemSummary {
    let r: IngestResponse = try await send(
      "/ingest", method: "POST", body: ["url": url.absoluteString, "share_text": note],
      timeout: Self.shareTimeout)
    return r.item
  }

  /// Multipart upload streamed from disk, so a large PDF never sits in memory.
  /// The server keeps `idempotencyKey` with the item, so sending the same
  /// upload again after a timeout returns that item instead of a second one.
  func ingestFile(
    at file: URL, name: String, mimeType: String, note: String?, idempotencyKey: String? = nil
  ) async throws -> ItemSummary {
    let boundary = "memorydump-\(UUID().uuidString)"
    let body = try Multipart.write(
      file: file, name: name, mimeType: mimeType, note: note, boundary: boundary)
    defer { try? FileManager.default.removeItem(at: body) }
    let (data, _) = try await authorized { token in
      var req = try self.request("/ingest/file", method: "POST", token: token, timeout: Self.shareTimeout)
      req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
      if let idempotencyKey { req.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key") }
      return req
    } perform: { req in
      try await self.http.upload(for: req, fromFile: body)
    }
    return try decoder.decode(IngestResponse.self, from: data).item
  }

  // MARK: Search and Ask

  func search(_ q: String, limit: Int = 25) async throws -> [SearchHit] {
    let r: SearchResponse = try await send("/search", method: "POST", body: SearchBody(q: q, limit: limit))
    return r.hits
  }

  /// `/ask/stream` as events. Cancel the consuming task to stop the request.
  /// `history` is the chat's turns before this question (see AskBody); a
  /// `scope` answers from that item or topic only, and is a 404 once it's gone.
  func askStream(
    _ question: String, history: [ChatTurn] = [], scope: AskScope? = nil
  ) -> AsyncThrowingStream<AskEvent, Error> {
    let body = AskBody(question: question, earlier: history, scope: scope)
    return AsyncThrowingStream { continuation in
      let task = Task {
        do {
          let (bytes, _) = try await authorized { token in
            var req = try self.request("/ask/stream", method: "POST", body: body, token: token)
            req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            return req
          } perform: { req in
            try await self.http.bytes(for: req)
          } detail: { bytes in
            var text = ""
            for try await line in bytes.lines { text += line }
            return Data(text.utf8)
          }
          var parser = SSEParser()
          for try await line in bytes.lines {
            if let event = try parser.feed(line) { continuation.yield(event) }
          }
          continuation.finish()
        } catch {
          continuation.finish(throwing: error)
        }
      }
      continuation.onTermination = { _ in task.cancel() }
    }
  }

  // MARK: Chats

  static let chatPageSize = 200

  /// Every chat, most recently changed first, read a page at a time.
  func chats() async throws -> [ChatSummary] {
    try await Self.everyPage(limit: Self.chatPageSize) { before in
      try await self.send("/chats", query: ["limit": String(Self.chatPageSize), "before": before?.lower])
    }
  }

  func chat(_ id: UUID) async throws -> Chat {
    try await send("/chats/\(id.lower)")
  }

  /// Creates or replaces the chat; the app picks the id. `chat.version` is the
  /// server's version this copy was built on (0 for a new chat); a 409 means
  /// another device saved it since. The answer carries the new version.
  func saveChat(_ chat: Chat) async throws -> ChatSummary {
    try await send(
      "/chats/\(chat.id.lower)", method: "PUT",
      body: ChatBody(turns: chat.turns, version: chat.version ?? 0))
  }

  func deleteChat(_ id: UUID) async throws {
    _ = try await data("/chats/\(id.lower)", method: "DELETE")
    forget("/chats/\(id.lower)")
  }

  // MARK: Wiki

  func wiki() async throws -> [TopicPageSummary] {
    try await send("/wiki")
  }

  func wikiPage(_ slug: String) async throws -> TopicPageDetail {
    try await send("/wiki/\(slug)")
  }

  // MARK: Account

  /// Ends this device's session on the server.
  func signOut() async throws {
    _ = try await data("/auth/signout", method: "POST")
  }

  func deleteAccount() async throws {
    _ = try await data("/auth/me", method: "DELETE")
  }

  // MARK: Devices and health

  func registerDevice(token: String, apnsEnv: String) async throws {
    let _: DeviceResponse = try await send(
      "/devices/register", method: "POST",
      body: ["push_token": token, "apns_env": apnsEnv, "platform": "ios"])
  }

  /// `/healthz` is unauthenticated and outside /v1.
  func health() async -> Bool {
    guard let req = try? request("/healthz", token: nil),
      let result = try? await http.data(for: req)
    else { return false }
    return (result.1 as? HTTPURLResponse)?.statusCode == 200
  }

  // MARK: Core

  private struct SearchBody: Encodable {
    let q: String
    let limit: Int
  }

  private struct ChatBody: Encodable {
    let turns: [ChatTurn]
    let version: Int
  }

  /// `saveAs` saves a write's answer as the copy of the GET at that path, for
  /// writes that answer with the thing they changed. `saves: false` keeps a
  /// GET's answer off the disk.
  private func send<T: Decodable>(
    _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil, saveAs: String? = nil, saves: Bool = true,
    timeout: TimeInterval? = nil
  ) async throws -> T {
    let data = try await data(
      path, method: method, query: query, body: body, saveAs: saveAs, saves: saves,
      timeout: timeout)
    return try decoder.decode(T.self, from: data)
  }

  private func data(
    _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil, saveAs: String? = nil, saves keep: Bool = true,
    timeout: TimeInterval? = nil
  ) async throws -> Data {
    let isRead = method == "GET"
    let key = keep ? (isRead ? Self.savedKey(path, query) : saveAs) : nil
    let user = store.current()?.user.id
    if savedOnly {
      guard isRead, let key, let user, let saved = saves.read(key, user: user) else {
        throw APIError.notSaved
      }
      return saved
    }
    // The copy a 304 stands for, and its ETag.
    var cached: (data: Data, etag: String)?
    if isRead, let key, let user, let etag = saves.etag(key, user: user),
      let data = saves.read(key, user: user)
    {
      cached = (data, etag)
    }
    do {
      let (data, resp) = try await authorized { token in
        var req = try self.request(
          path, method: method, query: query, body: body, token: token, timeout: timeout)
        if let cached { req.setValue(cached.etag, forHTTPHeaderField: "If-None-Match") }
        return req
      } perform: { req in
        try await self.http.data(for: req)
      }
      // Set only on a change: every set re-renders the views that show it.
      if isOffline { isOffline = false }
      // Unchanged since the saved copy; the server did answer, so not offline.
      if resp.statusCode == 304, let cached { return cached.data }
      // Not if the account signed out while this was on its way.
      if let key, let user, store.current()?.user.id == user {
        saves.write(data, key, user: user, etag: resp.value(forHTTPHeaderField: "ETag"))
      }
      return data
    } catch let error where error.isUnreachable {
      // Reads are what show saved copies; a write that fails changes none.
      if isRead, !isOffline { isOffline = true }
      // A read falls back to the saved copy; a write has to fail.
      if isRead, let key, let user, let saved = saves.read(key, user: user) { return saved }
      throw error
    }
  }

  /// Every page of a list that pages by `before`, the id of the previous
  /// page's last row, until a short page. When that row is gone by the time
  /// the next page is asked for, starts again from the top (a few times).
  static func everyPage<T: Identifiable>(
    limit: Int, _ page: (UUID?) async throws -> [T]
  ) async throws -> [T] where T.ID == UUID {
    var restarts = 0
    while true {
      do {
        var rows: [T] = []
        var before: UUID?
        while true {
          let next = try await page(before)
          rows += next
          guard next.count == limit, let last = next.last else { break }
          before = last.id
        }
        // A row that changed while paging can show up on two pages.
        var seen = Set<UUID>()
        return rows.filter { seen.insert($0.id).inserted }
      } catch let error where error.isUnknownCursor && restarts < 3 {
        restarts += 1
      }
    }
  }

  /// Lets requests out again after a 426, to find out whether the server
  /// still refuses this build.
  func checkBuildAgain() {
    updateRequired = nil
  }

  /// What a GET is saved under: its path and sorted query, without the server
  /// address or the /v1 prefix, so a new address or API version still finds
  /// the copies.
  static func savedKey(_ path: String, _ query: [String: String?]) -> String {
    let items = queryItems(query)
    guard !items.isEmpty else { return path }
    return path + "?" + items.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
  }

  /// Drops the saved copy of something deleted, so it can't be opened offline.
  private func forget(_ path: String) {
    guard let user = store.current()?.user.id else { return }
    saves.remove(Self.savedKey(path, [:]), user: user)
  }

  private static func queryItems(_ query: [String: String?]) -> [URLQueryItem] {
    query.compactMap { k, v in v.map { URLQueryItem(name: k, value: $0) } }
      .sorted { $0.name < $1.name }
  }

  /// Builds the request with the current access token, and after a 401 builds it
  /// again with a refreshed one. Throws APIError for any other non-2xx but a
  /// 304, which only a request with If-None-Match gets.
  private func authorized<R>(
    _ make: (String) throws -> URLRequest,
    perform: (URLRequest) async throws -> (R, URLResponse),
    detail: (R) async throws -> Data = { $0 as? Data ?? Data() }
  ) async throws -> (R, HTTPURLResponse) {
    guard !savedOnly else { throw APIError.notSaved }
    if let updateRequired { throw APIError.updateRequired(updateRequired) }
    guard var session = store.current() else { throw APIError.signedOut }
    var (result, resp) = try await perform(make(session.accessToken))
    if (resp as? HTTPURLResponse)?.statusCode == 401 {
      session = try await store.refresh(after: session)
      (result, resp) = try await perform(make(session.accessToken))
    }
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard let http = resp as? HTTPURLResponse, (200..<300).contains(status) || status == 304 else {
      if status == 401 {
        // Refused even with fresh tokens: the account behind the session is gone.
        store.expire()
        throw APIError.signedOut
      }
      let error = APIError(status: status, body: try await detail(result), response: resp as? HTTPURLResponse)
      if case .updateRequired(let message) = error, updateRequired == nil {
        updateRequired = message
        onUpdateRequired?(error.localizedDescription)
      }
      throw error
    }
    return (result, http)
  }

  /// Outside the API's /v1: the routes SuperTokens serves itself, and the
  /// health check.
  private static let unversioned: Set<String> = [
    "/auth/signin", "/auth/signup", "/auth/signout", "/auth/session/refresh", "/healthz",
  ]

  /// Where a path is on the server. Callers and saved copies use the path
  /// without /v1.
  static func route(_ path: String) -> String {
    unversioned.contains(path) ? path : "/v1" + path
  }

  func request(
    _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil, token: String?, timeout: TimeInterval? = nil
  ) throws -> URLRequest {
    var url = Server.url.appending(path: Self.route(path))
    let items = Self.queryItems(query)
    if !items.isEmpty { url.append(queryItems: items) }
    var req = URLRequest(url: url)
    req.httpMethod = method
    // The server answers builds it no longer supports with 426.
    req.setValue(Self.build, forHTTPHeaderField: "X-App-Build")
    // A read has a saved copy to fall back on, so don't wait the default
    // minute on a server that's asleep or off the network.
    if let timeout {
      req.timeoutInterval = timeout
    } else if method == "GET" {
      req.timeoutInterval = 10
    }
    if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    if let body {
      req.setValue("application/json", forHTTPHeaderField: "Content-Type")
      req.httpBody = try JSONEncoder().encode(body)
    }
    return req
  }
}

enum Multipart {
  /// Writes a multipart/form-data body to a temporary file: the file part,
  /// copied in 1 MB chunks, then an optional `share_text` field.
  static func write(file: URL, name: String, mimeType: String, note: String?, boundary: String)
    throws -> URL
  {
    let out = FileManager.default.temporaryDirectory.appending(path: "upload-\(UUID().uuidString)")
    FileManager.default.createFile(atPath: out.path(percentEncoded: false), contents: nil)
    let writer = try FileHandle(forWritingTo: out)
    defer { try? writer.close() }
    let safeName = name.replacing("\"", with: "")
    try writer.write(contentsOf: Data(
      "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: \(mimeType)\r\n\r\n"
        .utf8))
    let reader = try FileHandle(forReadingFrom: file)
    defer { try? reader.close() }
    while let chunk = try reader.read(upToCount: 1 << 20), !chunk.isEmpty {
      try writer.write(contentsOf: chunk)
    }
    if let note, !note.isEmpty {
      try writer.write(contentsOf: Data(
        "\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"share_text\"\r\n\r\n\(note)"
          .utf8))
    }
    try writer.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    return out
  }
}
