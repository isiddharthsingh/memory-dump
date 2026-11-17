import Foundation
import Testing

@testable import MemoryDump

/// The server, the account and the disk, as AskModel sees them through
/// `AskBackend`: what it was sent, and what it gets back.
private final class FakeServer {
  var user: UUID? = UUID()
  /// The chats the server has.
  var chats: [UUID: Chat] = [:]
  /// Every save tried, taken or not; every chat fetched; every delete tried.
  var puts: [Chat] = []
  var gets: [UUID] = []
  var deletes: [UUID] = []
  /// No request gets through.
  var isDown = false
  /// The next saves fail with these, in order.
  var failures: [Error] = []
  /// The next lists fail with these, in order.
  var listFailures: [Error] = []
  /// Run while a save is on its way, before the server answers it.
  var duringSave: (() -> Void)?
  let saves = SavedResponses(
    root: FileManager.default.temporaryDirectory.appending(path: "saved-\(UUID().uuidString)"))

  private var unreachable: Error { URLError(.notConnectedToInternet) }

  private func summary(_ chat: Chat) -> ChatSummary {
    ChatSummary(
      id: chat.id, questions: chat.turns.map(\.question), createdAt: chat.createdAt, updatedAt: chat.updatedAt,
      version: chat.version ?? 0)
  }

  /// A chat the server has, at this version, with its copy on the phone.
  func has(_ chat: Chat, version: Int, copyOnPhone: Bool = true) {
    var saved = chat
    saved.version = version
    chats[chat.id] = saved
    if copyOnPhone, let user { saves.keep(saved, as: summary(saved), user: user) }
  }

  var backend: AskBackend {
    AskBackend(
      user: { self.user },
      ask: { _, _, _ in
        AsyncThrowingStream { continuation in
          if self.isDown {
            continuation.finish(throwing: self.unreachable)
          } else {
            continuation.yield(.token("An answer"))
            continuation.yield(.done([]))
            continuation.finish()
          }
        }
      },
      chats: {
        if self.isDown { throw self.unreachable }
        if !self.listFailures.isEmpty { throw self.listFailures.removeFirst() }
        return self.chats.values.map(self.summary).sorted { $0.updatedAt > $1.updatedAt }
      },
      chat: { id in
        self.gets.append(id)
        if self.isDown { throw self.unreachable }
        guard let chat = self.chats[id] else { throw APIError.http(404, "Chat not found") }
        return chat
      },
      saveChat: { chat in
        self.puts.append(chat)
        let user = self.user
        self.duringSave?()
        self.duringSave = nil
        if self.isDown { throw self.unreachable }
        if !self.failures.isEmpty { throw self.failures.removeFirst() }
        var saved = chat
        saved.version = (self.chats[chat.id]?.version ?? 0) + 1
        self.chats[chat.id] = saved
        // As APIClient.saveChat does, and not for an account that left meanwhile.
        if let user, self.user == user { self.saves.keep(saved, as: self.summary(saved), user: user) }
        return self.summary(saved)
      },
      deleteChat: { id in
        self.deletes.append(id)
        if self.isDown { throw self.unreachable }
        guard self.chats.removeValue(forKey: id) != nil else { throw APIError.http(404, "Chat not found") }
      },
      savedChats: { [] },
      savedChat: { id in
        guard let user = self.user else { return nil }
        return self.saves.read("/chats/\(id.lower)", user: user)
          .flatMap { try? JSONDecoder.api.decode(Chat.self, from: $0) }
      },
      saves: saves)
  }
}

/// What AskModel does with a chat whose save failed, and when it reads a
/// chat from the phone instead of the server.
@Suite struct AskModelTests {
  private let server = FakeServer()

  private func chat(_ questions: [String] = ["What did I save?"], version: Int? = nil) -> Chat {
    Chat(
      id: UUID(), turns: questions.map { ChatTurn(id: UUID(), question: $0, answer: "An essay", status: .done) },
      createdAt: .now, updatedAt: .now, version: version)
  }

  /// Chats whose save failed before the app last quit.
  private func held(_ chats: Chat...) throws {
    server.saves.keepUnsaved(Dictionary(uniqueKeysWithValues: chats.map { ($0.id, $0) }), user: try #require(server.user))
  }

  private func heldNow() throws -> [UUID: Chat] {
    server.saves.unsavedChats(user: try #require(server.user))
  }

  /// Waits for what the model has under way: its answer, then every save queued.
  private func settled(_ model: AskModel, puts: Int = 0) async {
    for _ in 0..<2000 where server.puts.count < puts || model.isStreaming {
      try? await Task.sleep(for: .milliseconds(1))
    }
    // `open` waits for the queued saves; this chat isn't anywhere, so nothing else happens.
    for _ in 0..<3 { try? await model.open(UUID()) }
    server.gets.removeAll { server.chats[$0] == nil }
  }

  // MARK: Chats whose save failed

  @Test func heldChatsAreListedAndOpenWithNoServer() async throws {
    let a = chat(["Why?", "And then?"])
    try held(a)
    server.isDown = true
    let model = AskModel(backend: server.backend)

    await model.loadHistory()
    #expect(model.history?.map(\.id) == [a.id])
    try await model.open(a.id)
    #expect(model.turns.map(\.question) == ["Why?", "And then?"])
    #expect(server.gets.isEmpty)
    #expect(try heldNow().keys.contains(a.id))
  }

  @Test func aHeldChatIsSentOnceHoweverOftenItIsAsked() async throws {
    let a = chat()
    try held(a)
    let model = AskModel(backend: server.backend)

    for _ in 0..<3 { model.sendUnsaved() }
    await settled(model, puts: 1)
    #expect(server.puts.map(\.id) == [a.id])
    #expect(server.chats[a.id] != nil)
    #expect(try heldNow().isEmpty)
  }

  /// One still failing isn't queued again behind itself, which would make
  /// everything after it wait out a timeout per try.
  @Test func aHeldChatThatStillFailsIsTriedOncePerTurn() async throws {
    let a = chat()
    try held(a)
    server.isDown = true
    let model = AskModel(backend: server.backend)

    for _ in 0..<3 { model.sendUnsaved() }
    await settled(model, puts: 1)
    #expect(server.puts.count == 1)
    #expect(try heldNow().keys.contains(a.id))
    // Its turn is over, so it can be tried again.
    model.sendUnsaved()
    await settled(model, puts: 2)
    #expect(server.puts.count == 2)
  }

  /// The question's save fails, then the chat is sent again from the held
  /// ones: with what is held by then, not what was held when it was queued.
  @Test func aHeldChatIsSentWithItsLatestTurns() async throws {
    let a = chat(["First?"])
    try held(a)
    let model = AskModel(backend: server.backend)
    try await model.open(a.id)

    server.failures = [URLError(.timedOut)]
    model.submit("Second?")
    model.sendUnsaved()
    await settled(model, puts: 3)
    #expect(server.puts.prefix(2).map(\.turns.count) == [2, 2])
    #expect(server.chats[a.id]?.turns.map(\.question) == ["First?", "Second?"])
    #expect(server.chats[a.id]?.turns.last?.answer == "An answer")
    #expect(try heldNow().isEmpty)
  }

  @Test func aSaveThatGoesThroughSendsTheHeldChats() async throws {
    let earlier = chat(["Asked with no network"])
    try held(earlier)
    let model = AskModel(backend: server.backend)

    model.submit("Asked once it was back")
    await settled(model, puts: 3)
    #expect(server.chats[earlier.id]?.turns.map(\.question) == ["Asked with no network"])
    #expect(try heldNow().isEmpty)
  }

  /// Another account signs in while a save is on its way.
  @Test func theHeldChatsStayOutOfAnotherAccount() async throws {
    let (x, y) = (chat(["X?"]), chat(["Y?"]))
    try held(x, y)
    let other = UUID()
    let model = AskModel(backend: server.backend)

    server.duringSave = { self.server.user = other }
    model.sendUnsaved()
    await settled(model, puts: 1)
    #expect(server.puts.count == 1)
    #expect(server.saves.unsavedChats(user: other).isEmpty)
  }

  @Test func aChatTheServerNeverTookIsDeletedWithoutIt() async throws {
    let (never, once) = (chat(["Never sent"]), chat(["Sent once"], version: 2))
    try held(never, once)
    server.isDown = true
    let model = AskModel(backend: server.backend)

    await model.delete(never.id)
    #expect(try Set(heldNow().keys) == [once.id])
    // The server has this one, so only the server can delete it.
    await model.delete(once.id)
    #expect(try Set(heldNow().keys) == [once.id])
    #expect(server.deletes == [never.id, once.id])
  }

  // MARK: Opening from the phone's copy

  @Test func opensTheCopyAtTheVersionTheServerLists() async throws {
    let a = chat()
    server.has(a, version: 4)
    let model = AskModel(backend: server.backend)

    await model.loadHistory()
    try await model.open(a.id)
    #expect(model.chatId == a.id)
    #expect(model.chat?.version == 4)
    #expect(server.gets.isEmpty)
  }

  @Test func fetchesAChatAnotherDeviceSaved() async throws {
    var a = chat()
    server.has(a, version: 4)
    a.turns.append(ChatTurn(id: UUID(), question: "From the other phone?", answer: "Yes", status: .done))
    server.has(a, version: 5, copyOnPhone: false)
    let model = AskModel(backend: server.backend)

    await model.loadHistory()
    try await model.open(a.id)
    #expect(server.gets == [a.id])
    #expect(model.turns.count == 2)
  }

  /// The copy is only as good as the list it is checked against.
  @Test func fetchesUntilTheServerHasListedTheChats() async throws {
    let a = chat()
    server.has(a, version: 4)
    let model = AskModel(backend: server.backend)

    try await model.open(a.id)
    #expect(server.gets == [a.id])

    await model.loadHistory()
    model.newChat()
    server.listFailures = [URLError(.timedOut)]
    await model.loadHistory()
    try await model.open(a.id)
    #expect(server.gets == [a.id, a.id])
  }
}
