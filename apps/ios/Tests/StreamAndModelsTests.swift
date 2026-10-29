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

  @Test func decodesTokenResponseIntoSession() throws {
    let json = #"""
      {"access_token": "a", "refresh_token": "r", "token_type": "bearer", "expires_in": 900,
       "user": {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "email": "e@x.com",
                "display_name": null, "created_at": "2026-10-28T19:54:13.728123Z"}}
      """#
    let session = try JSONDecoder.api.decode(TokenResponse.self, from: Data(json.utf8)).session
    #expect(session.accessToken == "a")
    #expect(session.refreshToken == "r")
    #expect(session.user.email == "e@x.com")
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
