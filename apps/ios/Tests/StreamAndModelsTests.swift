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
        "created_at": "2026-10-29T02:10:00Z", "updated_at": "2026-10-29T02:11:00.5+00:00"}]
      """#
    let history = try JSONDecoder.api.decode([ChatSummary].self, from: Data(json.utf8))
    #expect(history.first?.title == "First?")
    #expect(history.first?.questions.count == 2)
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
