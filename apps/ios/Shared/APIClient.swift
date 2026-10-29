import Foundation

enum APIError: LocalizedError, Equatable {
  case notConfigured
  case signedOut
  /// The server answered with a non-2xx status. The text is FastAPI's `detail`.
  case http(Int, String)

  var errorDescription: String? {
    switch self {
    case .notConfigured: "Server URL not configured."
    case .signedOut: "Not signed in."
    case .http(let status, let detail): detail.isEmpty ? "HTTP \(status)" : detail
    }
  }
}

/// The backend API. Every authorized call adds the access token, and after a
/// 401 refreshes it once (see SessionStore.refresh) and tries again.
final class APIClient {
  static let shared = APIClient()

  private let store = SessionStore.shared
  private let http = URLSession.shared
  private let decoder = JSONDecoder.api

  // MARK: Items

  func listItems(
    limit: Int? = nil, offset: Int? = nil, status: String? = nil, tag: String? = nil,
    source: String? = nil
  ) async throws -> [ItemSummary] {
    let query: [String: String?] = [
      "limit": limit.map(String.init), "offset": offset.map(String.init), "status": status,
      "tag": tag, "source": source,
    ]
    return try await send("/items", query: query)
  }

  func item(_ id: UUID) async throws -> ItemDetail {
    try await send("/items/\(id.lower)")
  }

  func retryItem(_ id: UUID) async throws -> ItemDetail {
    try await send("/items/\(id.lower)/retry", method: "POST")
  }

  func updateNote(_ id: UUID, note: String?) async throws -> ItemDetail {
    try await send("/items/\(id.lower)", method: "PATCH", body: ["share_text": note])
  }

  func deleteItem(_ id: UUID) async throws {
    _ = try await data("/items/\(id.lower)", method: "DELETE")
  }

  func related(_ id: UUID) async throws -> RelatedResponse {
    try await send("/items/\(id.lower)/related")
  }

  func tags() async throws -> [TagCount] {
    let r: TagsResponse = try await send("/items/tags")
    return r.tags
  }

  // MARK: Ingest

  func ingest(_ url: URL, note: String?) async throws -> ItemSummary {
    let r: IngestResponse = try await send(
      "/ingest", method: "POST", body: ["url": url.absoluteString, "share_text": note])
    return r.item
  }

  /// Multipart upload streamed from disk, so a large PDF never sits in memory.
  func ingestFile(at file: URL, name: String, mimeType: String, note: String?) async throws
    -> ItemSummary
  {
    let boundary = "memorydump-\(UUID().uuidString)"
    let body = try Multipart.write(
      file: file, name: name, mimeType: mimeType, note: note, boundary: boundary)
    defer { try? FileManager.default.removeItem(at: body) }
    let (data, _) = try await authorized { base, token in
      var req = try self.request(base, "/ingest/file", method: "POST", token: token)
      req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
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
  func askStream(_ question: String) -> AsyncThrowingStream<AskEvent, Error> {
    AsyncThrowingStream { continuation in
      let task = Task {
        do {
          let (bytes, _) = try await authorized { base, token in
            var req = try self.request(
              base, "/ask/stream", method: "POST", body: ["question": question], token: token)
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

  // MARK: Wiki

  func wiki() async throws -> [TopicPageSummary] {
    try await send("/wiki")
  }

  func wikiPage(_ slug: String) async throws -> TopicPageDetail {
    try await send("/wiki/\(slug)")
  }

  // MARK: Devices and health

  func registerDevice(token: String, apnsEnv: String) async throws {
    let _: DeviceResponse = try await send(
      "/devices/register", method: "POST",
      body: ["push_token": token, "apns_env": apnsEnv, "platform": "ios"])
  }

  /// `/healthz` is unauthenticated.
  func health() async -> Bool {
    guard let base = store.serverURL,
      let req = try? request(base, "/healthz", token: nil),
      let result = try? await http.data(for: req)
    else { return false }
    return (result.1 as? HTTPURLResponse)?.statusCode == 200
  }

  // MARK: Core

  private struct SearchBody: Encodable {
    let q: String
    let limit: Int
  }

  private func send<T: Decodable>(
    _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil
  ) async throws -> T {
    let data = try await data(path, method: method, query: query, body: body)
    return try decoder.decode(T.self, from: data)
  }

  private func data(
    _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil
  ) async throws -> Data {
    let (data, _) = try await authorized { base, token in
      try self.request(base, path, method: method, query: query, body: body, token: token)
    } perform: { req in
      try await self.http.data(for: req)
    }
    return data
  }

  /// Builds the request with the current access token, and after a 401 builds it
  /// again with a refreshed one. Throws APIError.http for any other non-2xx.
  private func authorized<R>(
    _ make: (URL, String) throws -> URLRequest,
    perform: (URLRequest) async throws -> (R, URLResponse),
    detail: (R) async throws -> Data = { $0 as? Data ?? Data() }
  ) async throws -> (R, HTTPURLResponse) {
    guard let base = store.serverURL else { throw APIError.notConfigured }
    guard var session = store.current() else { throw APIError.signedOut }
    var (result, resp) = try await perform(make(base, session.accessToken))
    if (resp as? HTTPURLResponse)?.statusCode == 401 {
      session = try await store.refresh(after: session)
      (result, resp) = try await perform(make(base, session.accessToken))
    }
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard let http = resp as? HTTPURLResponse, (200..<300).contains(status) else {
      if status == 401 { throw APIError.signedOut }
      throw APIError.http(status, FastAPIError.detail(in: try await detail(result)))
    }
    return (result, http)
  }

  fileprivate func request(
    _ base: URL, _ path: String, method: String = "GET", query: [String: String?] = [:],
    body: (any Encodable)? = nil, token: String?
  ) throws -> URLRequest {
    var url = base.appending(path: path)
    let items = query.compactMap { k, v in v.map { URLQueryItem(name: k, value: $0) } }
    if !items.isEmpty { url.append(queryItems: items.sorted { $0.name < $1.name }) }
    var req = URLRequest(url: url)
    req.httpMethod = method
    if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    if let body {
      req.setValue("application/json", forHTTPHeaderField: "Content-Type")
      req.httpBody = try JSONEncoder().encode(body)
    }
    return req
  }
}

/// Unauthenticated `/auth/*` calls. They take the server address explicitly so
/// sign-in can try an address before it is saved.
enum AuthAPI {
  static func login(base: URL, email: String, password: String) async throws -> Session {
    try session(await post(base, "/auth/login", ["email": email, "password": password]))
  }

  static func signup(base: URL, email: String, password: String, displayName: String?)
    async throws -> Session
  {
    try session(
      await post(
        base, "/auth/signup",
        ["email": email, "password": password, "display_name": displayName]))
  }

  static func refresh(base: URL, refreshToken: String) async throws -> Session {
    try session(await post(base, "/auth/refresh", ["refresh_token": refreshToken]))
  }

  /// Best effort: the local session is cleared either way.
  static func logout(base: URL, refreshToken: String) async {
    _ = try? await post(base, "/auth/logout", ["refresh_token": refreshToken])
  }

  static func deleteAccount(base: URL, accessToken: String) async throws {
    var req = URLRequest(url: base.appending(path: "/auth/me"))
    req.httpMethod = "DELETE"
    req.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    let (data, resp) = try await URLSession.shared.data(for: req)
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(status) else {
      throw APIError.http(status, FastAPIError.detail(in: data))
    }
  }

  private static func session(_ data: Data) throws -> Session {
    try JSONDecoder.api.decode(TokenResponse.self, from: data).session
  }

  private static func post(_ base: URL, _ path: String, _ body: [String: String?]) async throws
    -> Data
  {
    var req = URLRequest(url: base.appending(path: path))
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try JSONEncoder().encode(body)
    let (data, resp) = try await URLSession.shared.data(for: req)
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(status) else {
      throw APIError.http(status, FastAPIError.detail(in: data))
    }
    return data
  }
}

/// FastAPI errors look like {"detail": "..."}; validation errors carry a list.
enum FastAPIError {
  static func detail(in data: Data) -> String {
    guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
      return String(data: data, encoding: .utf8) ?? ""
    }
    if let s = obj["detail"] as? String { return s }
    if let list = obj["detail"] as? [[String: Any]], let msg = list.first?["msg"] as? String {
      return msg
    }
    return ""
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
