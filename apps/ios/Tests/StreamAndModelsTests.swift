import Foundation
import Testing

@testable import MemoryDump

@Suite struct SSEParserTests {
  /// The server's frames, as AsyncBytes.lines hands them over: no blank lines.
  @Test func readsTokensThenDone() throws {
    var parser = SSEParser()
    let lines = [
      "event: token", #"data: {"text": "Hello"}"#,
      "event: token", #"data: {"text": " world"}"#,
      "event: done",
      #"data: {"citations": [{"kind": "item", "id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "title": "A", "url": "https://a.com", "slug": null}]}"#,
    ]
    let events = try lines.compactMap { try parser.feed($0) }
    #expect(events.count == 3)
    #expect(events[0] == .token("Hello"))
    #expect(events[1] == .token(" world"))
    guard case .done(let citations) = events[2] else { Issue.record("no done event"); return }
    #expect(citations.first?.key == "item:6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f")
  }

  @Test func blankLinesChangeNothing() throws {
    var parser = SSEParser()
    let lines = ["event: token", #"data: {"text": "a"}"#, "", "", "event: error", #"data: {"message": "boom"}"#, ""]
    let events = try lines.compactMap { try parser.feed($0) }
    #expect(events == [.token("a"), .error("boom")])
  }

  @Test func eventNameResetsAfterEachFrame() throws {
    var parser = SSEParser()
    _ = try parser.feed("event: token")
    _ = try parser.feed(#"data: {"text": "a"}"#)
    // A data line with no event line is a plain "message", which Ask ignores.
    #expect(try parser.feed(#"data: {"text": "b"}"#) == nil)
  }

  @Test func badCitationsKeepTheAnswer() throws {
    var parser = SSEParser()
    _ = try parser.feed("event: done")
    #expect(try parser.feed(#"data: {"citations": [{"kind": 1}]}"#) == .done([]))
  }
}

@Suite struct ModelTests {
  @Test func decodesItemWithMicrosecondsAndSnakeCase() throws {
    let json = #"""
      {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "url": "https://a.com/x", "source": "webpage",
       "status": "ready", "title": null, "summary": "S", "thumbnail_url": "https://a.com/t.png",
       "tags": ["ai"], "created_at": "2026-10-28T19:54:13.728123Z"}
      """#
    let item = try JSONDecoder.api.decode(ItemSummary.self, from: Data(json.utf8))
    #expect(item.thumbnailUrl == "https://a.com/t.png")
    #expect(item.error == nil)
    #expect(abs(item.createdAt.timeIntervalSince1970 - 1_793_217_253.728) < 0.001)
  }

  @Test(arguments: [
    "2026-10-28T19:54:13Z",
    "2026-10-28T19:54:13.7Z",
    "2026-10-28T19:54:13.728123+00:00",
    "2026-10-28T19:54:13.728123",
  ])
  func parsesServerDates(_ raw: String) {
    #expect(ISODate.parse(raw) != nil)
  }

  @Test func readsSuperTokensSignInResults() throws {
    func problem(_ json: String) throws -> String? {
      try JSONDecoder().decode(AuthResult.self, from: Data(json.utf8)).problem
    }
    #expect(try problem(#"{"status": "OK", "user": {"id": "u"}}"#) == nil)
    #expect(try problem(#"{"status": "WRONG_CREDENTIALS_ERROR"}"#) == "Incorrect email or password.")
    #expect(
      try problem(
        #"{"status": "FIELD_ERROR", "formFields": [{"id": "email", "error": "This email already exists. Please sign in instead."}]}"#
      ) == "This email already exists. Please sign in instead.")
    #expect(try problem(#"{"status": "GENERAL_ERROR", "message": "Signups are paused"}"#) == "Signups are paused")
  }

  /// `/auth/me` is a FastAPI response, read with JSONDecoder.api.
  @Test func decodesProfile() throws {
    let json = #"""
      {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "email": "e@x.com",
       "display_name": null, "created_at": "2026-10-28T19:54:13.728123Z"}
      """#
    let user = try JSONDecoder.api.decode(User.self, from: Data(json.utf8))
    #expect(user.email == "e@x.com")
    #expect(user.displayName == nil)
  }

  /// SessionStore writes with JSONEncoder.api and reads with JSONDecoder.api.
  @Test func sessionRoundTripsThroughTheKeychainEncoding() throws {
    let user = User(id: UUID(), email: "e@x.com", displayName: nil, createdAt: Date(timeIntervalSince1970: 1_793_217_253))
    let session = Session(accessToken: "a", refreshToken: "r", user: user)
    let data = try JSONEncoder.api.encode(session)
    #expect(try JSONDecoder.api.decode(Session.self, from: data) == session)
  }

  @Test func readsFastAPIErrorDetail() {
    #expect(FastAPIError.detail(in: Data(#"{"detail": "Item not found"}"#.utf8)) == "Item not found")
    #expect(FastAPIError.detail(in: Data(#"{"detail": [{"msg": "bad email"}]}"#.utf8)) == "bad email")
    #expect(FastAPIError.detail(in: Data("oops".utf8)) == "oops")
    #expect(FastAPIError.detail(in: Data(#"{"message": "try refresh token"}"#.utf8)) == "try refresh token")
  }

  @Test func readsRetryAfterSeconds() {
    #expect(APIError.seconds(retryAfter: "120") == 120)
    #expect(APIError.seconds(retryAfter: " 5 ") == 5)
    #expect(APIError.seconds(retryAfter: "0") == 0)
    #expect(APIError.seconds(retryAfter: nil) == nil)
    #expect(APIError.seconds(retryAfter: "-3") == nil)
    // The HTTP-date form, which the server doesn't send.
    #expect(APIError.seconds(retryAfter: "Wed, 21 Oct 2026 07:28:00 GMT") == nil)
  }

  @Test func buildsErrorsFromResponses() throws {
    func response(_ status: Int, _ headers: [String: String] = [:]) -> HTTPURLResponse? {
      HTTPURLResponse(url: URL(string: "https://a.com/v1/ingest")!, statusCode: status, httpVersion: nil, headerFields: headers)
    }
    let limited = APIError(
      status: 429, body: Data(#"{"detail": "Too many attempts. Try again later."}"#.utf8),
      response: response(429, ["Retry-After": "30"]))
    #expect(limited == .http(429, "Too many attempts. Try again later.", retryAfter: 30))
    #expect(limited.localizedDescription == "Too many attempts. Try again later.")
    #expect(limited.retryAfter == 30)
    #expect(APIError(status: 429, body: Data(), response: response(429)).localizedDescription == "Too many tries. Try again later.")
    let old = APIError(status: 426, body: Data(#"{"detail": "Update the app."}"#.utf8), response: response(426))
    #expect(old == .updateRequired("Update the app."))
    #expect(old.localizedDescription == "Update the app.")
    #expect(APIError(status: 404, body: Data(#"{"detail": "Unknown cursor"}"#.utf8), response: response(404)).isUnknownCursor)
    #expect(!APIError.http(404, "Item not found").isUnknownCursor)
  }

  /// The app's own routes are under /v1; SuperTokens' stay where it serves them.
  @Test func versionsAppRoutesOnly() {
    #expect(APIClient.route("/items") == "/v1/items")
    #expect(APIClient.route("/ingest/file") == "/v1/ingest/file")
    #expect(APIClient.route("/auth/me") == "/v1/auth/me")
    #expect(APIClient.route("/auth/signin") == "/auth/signin")
    #expect(APIClient.route("/auth/signup") == "/auth/signup")
    #expect(APIClient.route("/auth/signout") == "/auth/signout")
    #expect(APIClient.route("/auth/session/refresh") == "/auth/session/refresh")
  }

  @Test func decodesItemChanges() throws {
    let json = #"""
      {"full": false, "changed": ["6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f"], "deleted": [],
       "until": "2026-10-30T12:00:00.123456Z"}
      """#
    let changes = try JSONDecoder.api.decode(ItemChanges.self, from: Data(json.utf8))
    #expect(!changes.full)
    #expect(changes.changed.count == 1)
    #expect(changes.until == "2026-10-30T12:00:00.123456Z")
  }

  @Test func writesMultipartBody() throws {
    let src = FileManager.default.temporaryDirectory.appending(path: "src-\(UUID().uuidString).pdf")
    try Data("PDFDATA".utf8).write(to: src)
    let out = try Multipart.write(file: src, name: "a.pdf", mimeType: "application/pdf", note: "hi", boundary: "B")
    let body = try String(contentsOf: out, encoding: .utf8)
    #expect(body.hasPrefix("--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\nContent-Type: application/pdf\r\n\r\nPDFDATA"))
    #expect(body.contains("name=\"share_text\"\r\n\r\nhi"))
    #expect(body.hasSuffix("\r\n--B--\r\n"))
  }
}

@Suite struct ChatCodingTests {
  /// GET /chats/{id} as the server sends it.
  @Test func decodesServerChatAndSettlesCutOffAnswers() throws {
    let json = #"""
      {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "created_at": "2026-10-29T02:10:00.123456Z",
       "updated_at": "2026-10-29T02:11:00Z", "turns": [
        {"id": "0b9a2f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "question": "What did I save?", "answer": "Two essays",
         "citations": [{"kind": "item", "id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "title": "A", "url": null, "slug": null}],
         "status": "done", "error": null},
        {"id": "1b9a2f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "question": "And before?", "answer": "One",
         "citations": [], "status": "streaming", "error": null},
        {"id": "2b9a2f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "question": "Why?", "answer": "", "citations": [],
         "status": "failed", "error": {"title": "Offline", "detail": "No network"}}]}
      """#
    var chat = try JSONDecoder.api.decode(Chat.self, from: Data(json.utf8))
    #expect(chat.turns.map(\.question) == ["What did I save?", "And before?", "Why?"])
    #expect(chat.turns[0].citations.first?.title == "A")
    #expect(chat.turns[1].isStreaming)
    chat.settle()
    #expect(!chat.turns[1].isStreaming)
    #expect(chat.turns[1].answer == "One")
    guard case .failed(let title, let detail) = chat.turns[2].status else { Issue.record("lost the failure"); return }
    #expect(title == "Offline")
    #expect(detail == "No network")
  }

  @Test func settleOffersRetryWhenNoAnswerArrived() {
    var chat = Chat(id: UUID(), turns: [ChatTurn(id: UUID(), question: "Lost?")], createdAt: .now, updatedAt: .now)
    chat.settle()
    guard case .failed = chat.turns[0].status else { Issue.record("expected Try again"); return }
  }

  @Test func decodesHistorySummary() throws {
    let json = #"""
      [{"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "questions": ["First?", "Second?"],
        "created_at": "2026-10-29T02:10:00Z", "updated_at": "2026-10-29T02:11:00.5+00:00", "version": 4}]
      """#
    let history = try JSONDecoder.api.decode([ChatSummary].self, from: Data(json.utf8))
    #expect(history.first?.title == "First?")
    #expect(history.first?.questions.count == 2)
    #expect(history.first?.version == 4)
  }

  /// A chat saved on the phone before chats had versions still opens.
  @Test func decodesChatsWithAndWithoutVersion() throws {
    let old = #"{"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "created_at": "2026-10-29T02:10:00Z", "updated_at": "2026-10-29T02:11:00Z", "turns": []}"#
    #expect(try JSONDecoder.api.decode(Chat.self, from: Data(old.utf8)).version == nil)
    let new = old.replacing(#""turns": []"#, with: #""turns": [], "version": 7"#)
    #expect(try JSONDecoder.api.decode(Chat.self, from: Data(new.utf8)).version == 7)
  }

  @Test func mergeKeepsTheServersOrderAndThisDevicesTurns() {
    let (a, b, c, d) = (UUID(), UUID(), UUID(), UUID())
    let server = [
      ChatTurn(id: a, question: "A", answer: "server", status: .done),
      ChatTurn(id: b, question: "B", answer: "from the other phone", status: .done),
      ChatTurn(id: c, question: "C", answer: "server", status: .done),
    ]
    let local = [
      ChatTurn(id: c, question: "C", answer: "local", status: .done),
      ChatTurn(id: a, question: "A", answer: "local", status: .done),
      ChatTurn(id: d, question: "D", answer: "only here", status: .done),
    ]
    let merged = Chat.merge(server: server, local: local)
    #expect(merged.map(\.id) == [a, b, c, d])
    #expect(merged.map(\.answer) == ["local", "from the other phone", "local", "only here"])
    #expect(Chat.merge(server: [], local: local).map(\.id) == [c, a, d])
    #expect(Chat.merge(server: server, local: []).map(\.id) == [a, b, c])
  }

  /// PUT bodies go out through a plain JSONEncoder, so the keys must already be the server's.
  @Test func encodesTurnsWithTheServersKeys() throws {
    let failed = ChatTurn(id: UUID(), question: "Why?", status: .failed(title: "Offline", detail: "No network"))
    let f = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(failed)) as? [String: Any])
    #expect(Set(f.keys) == ["id", "question", "answer", "citations", "status", "error"])
    #expect(f["status"] as? String == "failed")
    #expect((f["error"] as? [String: String])?["title"] == "Offline")

    let done = ChatTurn(id: UUID(), question: "Hi", answer: "Hello", status: .done)
    let d = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(done)) as? [String: Any])
    #expect(d["status"] as? String == "done")
    #expect(d["error"] == nil)
  }
}

@Suite struct ShareTextTests {
  @Test func findsTheFirstWebURL() {
    #expect(ShareText.firstHTTPURL(in: "look https://x.com/a/status/1 and http://b.com")?.absoluteString == "https://x.com/a/status/1")
    #expect(ShareText.firstHTTPURL(in: "no link here") == nil)
  }

  @Test func linkNoteDropsTheURL() {
    let url = URL(string: "https://x.com/a/status/1")!
    #expect(ShareText.linkNote(from: "Great thread https://x.com/a/status/1", url: url) == "Great thread")
    #expect(ShareText.linkNote(from: "https://x.com/a/status/1", url: url) == nil)
    #expect(ShareText.linkNote(from: nil, url: url) == nil)
  }

  @Test func fileNoteIsTextWithoutURLs() {
    #expect(ShareText.fileNote(from: "  read chapter 3 ") == "read chapter 3")
    #expect(ShareText.fileNote(from: "see https://a.com") == nil)
    #expect(ShareText.fileNote(from: "   ") == nil)
  }
}

@Suite struct ShareQueueTests {
  @Test func sharesWaitWhenTryingAgainCanWork() {
    #expect(ShareQueue.waits(after: URLError(.timedOut)))
    #expect(ShareQueue.waits(after: URLError(.cannotFindHost)))
    #expect(ShareQueue.waits(after: URLError(.cancelled)))
    #expect(ShareQueue.waits(after: CancellationError()))
    #expect(ShareQueue.waits(after: APIError.signedOut))
    // A proxy in front of a stopped server, a busy one, and rate limits.
    #expect(ShareQueue.waits(after: APIError.http(502, "")))
    #expect(ShareQueue.waits(after: APIError.http(500, "")))
    #expect(ShareQueue.waits(after: APIError.http(429, "slow down")))
    #expect(ShareQueue.waits(after: APIError.http(408, "")))
    // Kept until the app is updated.
    #expect(ShareQueue.waits(after: APIError.updateRequired("Update")))
  }

  @Test func refusedSharesAreDropped() {
    #expect(!ShareQueue.waits(after: APIError.http(400, "bad url")))
    #expect(!ShareQueue.waits(after: APIError.http(413, "too large")))
    #expect(!ShareQueue.waits(after: APIError.http(422, "")))
    #expect(!ShareQueue.waits(after: DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: ""))))
  }

  @Test func onlyAnUnreachableServerHoldsUpTheQueue() {
    #expect(ShareQueue.holdsUpQueue(URLError(.timedOut)))
    #expect(ShareQueue.holdsUpQueue(APIError.http(503, "")))
    #expect(ShareQueue.holdsUpQueue(APIError.http(429, "")))
    #expect(ShareQueue.holdsUpQueue(APIError.signedOut))
    #expect(ShareQueue.holdsUpQueue(APIError.updateRequired("")))
    // One share the server chokes on, or one the other process is sending.
    #expect(!ShareQueue.holdsUpQueue(APIError.http(500, "")))
    #expect(!ShareQueue.holdsUpQueue(ShareQueue.InProgress()))
    #expect(ShareQueue.waits(after: ShareQueue.InProgress()))
  }

  @Test func anEntryAnotherSenderHoldsIsNotSentTwice() async throws {
    let entry = try ShareQueue.enqueueLink(URL(string: "https://example.com/locked")!, note: nil, title: nil)
    defer { ShareQueue.remove(entry) }
    let lock = AppGroup.directory("ShareQueue").appending(path: "\(entry.id.lower).lock")
    let fd = open(lock.path(percentEncoded: false), O_CREAT | O_RDWR, 0o600)
    defer { close(fd) }
    #expect(flock(fd, LOCK_EX | LOCK_NB) == 0)
    guard case .waiting(let error) = await ShareQueue.send(entry) else {
      Issue.record("sent an entry another sender held")
      return
    }
    #expect(error is ShareQueue.InProgress)
  }

  @Test func anEntryTheOtherProcessSentIsNotSentAgain() async throws {
    let entry = try ShareQueue.enqueueLink(URL(string: "https://example.com/gone")!, note: nil, title: nil)
    ShareQueue.remove(entry)
    guard case .waiting(let error) = await ShareQueue.send(entry) else {
      Issue.record("sent an entry that had left the queue")
      return
    }
    #expect(error is ShareQueue.InProgress)
  }

  @Test func readsEntriesQueuedBeforeTheyHadATitle() throws {
    let json = #"{"id":"79D14C05-0D65-4993-A998-D39D993620AE","queuedAt":815000000,"kind":{"link":{"_0":"https:\/\/x.com\/a\/status\/1"}}}"#
    let entry = try JSONDecoder().decode(QueuedShare.self, from: Data(json.utf8))
    #expect(entry.title == nil)
    #expect(QueuedShares.label(entry) == "x.com")
  }
}

@Suite struct OfflineCopyTests {
  /// A fresh folder per test; Swift Testing makes a new suite value for each.
  private let saves = SavedResponses(
    root: FileManager.default.temporaryDirectory.appending(path: "saved-\(UUID().uuidString)"))

  @Test func keepsEachAccountsCopiesApart() {
    let (a, b) = (UUID(), UUID())
    saves.write(Data("A".utf8), "/items", user: a)
    #expect(saves.read("/items", user: a) == Data("A".utf8))
    #expect(saves.read("/items", user: b) == nil)
    saves.write(Data("A2".utf8), "/items", user: a)
    #expect(saves.read("/items", user: a) == Data("A2".utf8))
    saves.remove("/items", user: a)
    #expect(saves.read("/items", user: a) == nil)
  }

  @Test func clearForgetsEveryAccount() {
    let (a, b) = (UUID(), UUID())
    saves.write(Data("A".utf8), "/wiki", user: a)
    saves.write(Data("B".utf8), "/wiki", user: b)
    saves.clear()
    #expect(saves.read("/wiki", user: a) == nil)
    #expect(saves.read("/wiki", user: b) == nil)
  }

  /// The Library's pages and the offline walk's pages must land on the same
  /// copy, and copies saved before /v1 keep their keys.
  @Test func savedKeyIgnoresQueryOrderAndMissingValues() {
    #expect(APIClient.savedKey("/items", ["limit": "25", "before": "abc", "tag": nil]) == "/items?before=abc&limit=25")
    #expect(APIClient.savedKey("/items", ["before": "abc", "limit": "25"]) == "/items?before=abc&limit=25")
    #expect(APIClient.savedKey("/items", ["limit": "25", "before": nil]) == "/items?limit=25")
    #expect(APIClient.savedKey("/wiki/llms", [:]) == "/wiki/llms")
  }

  @Test func keepsTheETagWithItsBody() {
    let user = UUID()
    saves.write(Data("v1".utf8), "/items/a", user: user, etag: #""abc""#)
    #expect(saves.etag("/items/a", user: user) == #""abc""#)
    #expect(saves.read("/items/a", user: user) == Data("v1".utf8))
    // A body that came without one leaves no stale ETag behind.
    saves.write(Data("v2".utf8), "/items/a", user: user)
    #expect(saves.etag("/items/a", user: user) == nil)
    saves.write(Data("v3".utf8), "/items/a", user: user, etag: "W/\"def\"")
    saves.remove("/items/a", user: user)
    #expect(saves.etag("/items/a", user: user) == nil)
    #expect(saves.read("/items/a", user: user) == nil)
  }

  /// Rows in pages of 2, newest first, the way /items and /chats page.
  private struct Row: Identifiable, Equatable { let id: UUID }

  @Test func pagesByTheLastRowUntilAShortPage() async throws {
    let rows = (0..<5).map { _ in Row(id: UUID()) }
    var asked: [UUID?] = []
    let all = try await APIClient.everyPage(limit: 2) { (before: UUID?) -> [Row] in
      asked.append(before)
      let start = before.flatMap { b in rows.firstIndex { $0.id == b } }.map { $0 + 1 } ?? 0
      return Array(rows[start..<min(start + 2, rows.count)])
    }
    #expect(all == rows)
    #expect(asked == [nil, rows[1].id, rows[3].id])
  }

  @Test func startsOverWhenTheCursorRowIsGone() async throws {
    let (a, b, c) = (Row(id: UUID()), Row(id: UUID()), Row(id: UUID()))
    // What the server answers, call by call.
    var answers: [Result<[Row], Error>] = [
      .success([a, b]), .failure(APIError.http(404, "Unknown cursor")),
      .success([a, b]), .success([b, c]), .success([]),
    ]
    var asked: [UUID?] = []
    let all = try await APIClient.everyPage(limit: 2) { (before: UUID?) -> [Row] in
      asked.append(before)
      return try answers.removeFirst().get()
    }
    #expect(asked == [nil, b.id, nil, b.id, c.id])
    // b moved between pages and came back twice.
    #expect(all == [a, b, c])
  }

  @Test func onlyAMissingServerFallsBackToTheSavedCopy() {
    #expect(URLError(.notConnectedToInternet).isUnreachable)
    #expect(URLError(.cannotConnectToHost).isUnreachable)
    #expect(URLError(.timedOut).isUnreachable)
    #expect(APIError.http(503, "").isUnreachable)
    #expect(!URLError(.cancelled).isUnreachable)
    #expect(!APIError.http(404, "Item not found").isUnreachable)
    #expect(!APIError.signedOut.isUnreachable)
  }

  /// The walk skips what matches, so a chat's date must survive the round trip.
  @Test func syncManifestRoundTrips() throws {
    var manifest = OfflineSync.Manifest()
    manifest.items[UUID()] = "ready"
    manifest.wiki["large-language-models"] = 3
    manifest.chats[UUID()] = try #require(ISODate.parse("2026-10-28T19:54:13.728123Z"))
    manifest.itemsUntil = "2026-10-30T12:00:00.123456Z"
    let data = try JSONEncoder().encode(manifest)
    #expect(try JSONDecoder().decode(OfflineSync.Manifest.self, from: data) == manifest)
  }
}
