import Foundation
import Testing

@testable import MemoryDump

/// The copies of chats the phone writes itself: a save the server took, and
/// the saves it hasn't taken yet.
@Suite struct SavedChatsTests {
  /// A fresh folder per test; Swift Testing makes a new suite value for each.
  private let saves = SavedResponses(
    root: FileManager.default.temporaryDirectory.appending(path: "saved-\(UUID().uuidString)"))
  private let user = UUID()
  private let day = Date(timeIntervalSince1970: 1_793_000_000)

  private func chat(_ id: UUID = UUID(), _ questions: [String] = ["What did I save?"], version: Int? = nil) -> Chat {
    Chat(
      id: id, turns: questions.map { ChatTurn(id: UUID(), question: $0, answer: "An essay", status: .done) },
      createdAt: day, updatedAt: day, version: version)
  }

  private func summary(_ chat: Chat, version: Int) -> ChatSummary {
    ChatSummary(
      id: chat.id, questions: chat.turns.map(\.question), createdAt: day, updatedAt: day.addingTimeInterval(60),
      version: version)
  }

  /// History's first page as `APIClient.chats` saves it, in pages of 3.
  private let listKey = "/chats?limit=3"

  private func list(_ rows: [ChatSummary]) throws {
    saves.write(try JSONEncoder.api.encode(rows), listKey, user: user)
  }

  private func listed() throws -> [ChatSummary] {
    try JSONDecoder.api.decode([ChatSummary].self, from: try #require(saves.read(listKey, user: user)))
  }

  // MARK: A save the server took

  /// The PUT answers with a summary, so the phone writes the chat's GET itself.
  @Test func aSavedChatReadsBackAsItsGET() throws {
    let sent = chat(version: 3)
    saves.write(Data("old".utf8), "/chats/\(sent.id.lower)", user: user, etag: #"W/"abc""#)
    saves.keep(sent, as: summary(sent, version: 4), user: user, pageSize: 3)

    let data = try #require(saves.read("/chats/\(sent.id.lower)", user: user))
    let copy = try JSONDecoder.api.decode(Chat.self, from: data)
    #expect(copy.id == sent.id)
    #expect(copy.turns.map(\.question) == ["What did I save?"])
    #expect(copy.turns.map(\.answer) == ["An essay"])
    #expect(copy.version == 4)
    #expect(copy.updatedAt == day.addingTimeInterval(60))
    // The old body's ETag would turn the next GET into a 304 for a body that's gone.
    #expect(saves.etag("/chats/\(sent.id.lower)", user: user) == nil)
  }

  @Test func aSavedChatGoesToTheTopOfHistorysCopy() throws {
    let (a, b) = (chat(), chat())
    try list([summary(a, version: 1), summary(b, version: 2)])

    let again = chat(b.id, ["First?", "Second?"])
    saves.keep(again, as: summary(again, version: 3), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [b.id, a.id])
    #expect(try listed().first?.questions == ["First?", "Second?"])
    #expect(try listed().first?.version == 3)
  }

  /// Only the saved chat's row is written by the phone, so the rest keep the
  /// server's dates to the microsecond and cost no decoding.
  @Test func theOtherRowsStayAsTheServerWroteThem() throws {
    let other = UUID()
    let json = #"""
      [{"id": "\#(other.lower)", "questions": ["Before?"], "created_at": "2026-10-29T02:10:00.123456Z",
        "updated_at": "2026-10-29T02:11:00.654321Z", "version": 3}]
      """#
    saves.write(Data(json.utf8), listKey, user: user)

    let sent = chat()
    saves.keep(sent, as: summary(sent, version: 1), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [sent.id, other])
    let raw = try JSONSerialization.jsonObject(with: try #require(saves.read(listKey, user: user)))
    let rows = try #require(raw as? [[String: Any]])
    #expect(rows.last?["updated_at"] as? String == "2026-10-29T02:11:00.654321Z")
  }

  /// `keep` rewrites the copy `APIClient.chats` reads its first page from.
  @Test func historysCopyIsUnderTheQueryItIsAskedWith() {
    let last = UUID()
    #expect(APIClient.savedKey("/chats", APIClient.chatsQuery(limit: 3)) == listKey)
    #expect(
      APIClient.savedKey("/chats", APIClient.chatsQuery(limit: 3, before: last)) == "/chats?before=\(last.lower)&limit=3")
  }

  /// A full page says there's a next one, saved under this page's last row.
  @Test func historysCopyKeepsItsPaging() throws {
    let (a, b, c, new) = (chat(), chat(), chat(), chat())
    // One more row would fill the page, and reading it would ask for a next page that was never saved.
    try list([summary(a, version: 1), summary(b, version: 1)])
    saves.keep(new, as: summary(new, version: 1), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [a.id, b.id])

    try list([summary(a, version: 1), summary(b, version: 1), summary(c, version: 1)])
    saves.keep(new, as: summary(new, version: 1), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [a.id, b.id, c.id])
    // The last row names the next page, so it stays where it is.
    saves.keep(c, as: summary(c, version: 2), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [a.id, b.id, c.id])
    // Any other row of a full page can move up.
    saves.keep(b, as: summary(b, version: 2), user: user, pageSize: 3)
    #expect(try listed().map(\.id) == [b.id, a.id, c.id])
    #expect(try listed().first?.version == 2)
  }

  /// One chat isn't the whole of History: with no list saved, none is made up.
  @Test func aSavedChatMakesNoHistoryOfItsOwn() {
    let sent = chat()
    saves.keep(sent, as: summary(sent, version: 1), user: user, pageSize: 3)
    #expect(saves.read(listKey, user: user) == nil)
  }

  // MARK: Opening without the server

  /// The sync only needs to know which version the phone has.
  @Test func aCopysVersionIsReadWithoutTheChat() {
    let sent = chat(version: 3)
    #expect(saves.chatVersion(sent.id, user: user) == nil)
    saves.keep(sent, as: summary(sent, version: 4), user: user, pageSize: 3)
    #expect(saves.chatVersion(sent.id, user: user) == 4)
    #expect(saves.chatVersion(sent.id, user: UUID()) == nil)
    // Saved before chats had versions.
    saves.write(Data(#"{"id": "\#(sent.id.lower)", "turns": []}"#.utf8), "/chats/\(sent.id.lower)", user: user)
    #expect(saves.chatVersion(sent.id, user: user) == nil)
  }

  @Test func aCopyStandsInOnlyAtTheVersionHistoryLists() {
    let copy = chat(version: 4)
    #expect(copy.isCurrent(for: summary(copy, version: 4)))
    // Another device saved since.
    #expect(!copy.isCurrent(for: summary(copy, version: 5)))
    // Deleted and saved again from another device, which starts its versions over.
    #expect(!copy.isCurrent(for: summary(copy, version: 1)))
    // Saved before chats had versions.
    #expect(!chat(copy.id).isCurrent(for: summary(copy, version: 1)))
    #expect(!copy.isCurrent(for: summary(chat(), version: 4)))
  }

  // MARK: Saves the server hasn't taken

  /// What asking with no network leaves: a turn that failed, and why.
  @Test func unsavedChatsOutliveTheApp() throws {
    let a = chat(version: 2)
    var b = chat(UUID(), ["Why?", "And then?"])
    b.turns[1].status = .failed(title: "Offline", detail: "No network")
    saves.keepUnsaved([a.id: a, b.id: b], user: user)

    let read = saves.unsavedChats(user: user)
    #expect(Set(read.keys) == [a.id, b.id])
    #expect(read[a.id]?.version == 2)
    let kept = try #require(read[b.id])
    #expect(kept.version == nil)
    #expect(kept.turns.map(\.question) == ["Why?", "And then?"])
    guard case .failed(let title, let detail) = kept.turns[1].status else { Issue.record("lost the failure"); return }
    #expect(title == "Offline")
    #expect(detail == "No network")
    #expect(saves.unsavedChats(user: UUID()).isEmpty)
  }

  @Test func sentChatsLeaveNothingBehind() {
    let a = chat()
    saves.keepUnsaved([a.id: a], user: user)
    saves.keepUnsaved([:], user: user)
    #expect(saves.unsavedChats(user: user).isEmpty)
  }
}
