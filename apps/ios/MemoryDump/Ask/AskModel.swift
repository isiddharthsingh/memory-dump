import Foundation

/// The Ask tab: the open chat, streamed answers, and History. Chats live on
/// the server, so they follow the account to any device.
@Observable
final class AskModel {
  typealias Turn = ChatTurn

  /// The open chat; nil is a new chat with no question yet.
  private(set) var chat: Chat?
  /// Every saved chat, most recent first; nil until History first loads.
  private(set) var history: [ChatSummary]?
  private(set) var historyError: Error?
  var input = ""
  private(set) var tags: [TagCount] = []
  private(set) var wiki: [TopicPageSummary] = []

  private var stream: Task<Void, Never>?
  /// Server writes run one after another, so an older copy of a chat never
  /// lands after a newer one, or after the chat is deleted.
  private var writes: Task<Void, Never>?
  /// Chats whose last save failed; sent again when History loads.
  private var unsaved: [UUID: Chat] = [:]

  var chatId: UUID? { chat?.id }
  var turns: [Turn] { chat?.turns ?? [] }
  var isStreaming: Bool { turns.contains(where: \.isStreaming) }
  var canSend: Bool { !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isStreaming }

  /// Wiki titles are cased phrases ("Large Language Models"); tags are slugs,
  /// so they are only a fallback for a library with no wiki yet.
  var suggestions: [String] {
    let topics = wiki.map { Format.topicTitle($0.title) }
    let subjects = topics.isEmpty ? tags.map { Format.humanizeTag($0.tag) } : topics
    return subjects.prefix(3).map { "What do I know about \($0)?" } + ["What did I save this week?"]
  }

  func loadSuggestions() async {
    async let t = try? APIClient.shared.tags()
    async let w = try? APIClient.shared.wiki()
    tags = await t ?? []
    wiki = await w ?? []
  }

  func loadHistory() async {
    for chat in unsaved.values { upload(chat) }
    let result = await afterWrites { () async -> Result<[ChatSummary], Error> in
      do { return .success(try await APIClient.shared.chats()) } catch { return .failure(error) }
    }.value
    switch result {
    case .success(let chats):
      history = chats
      historyError = nil
      for chat in unsaved.values { record(chat) }
    case .failure(let error):
      if history == nil { historyError = error }
    }
  }

  func submit(_ raw: String) {
    let question = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !question.isEmpty, !isStreaming else { return }
    Haptics.press()
    let turn = Turn(id: UUID(), question: question)
    var chat = self.chat ?? Chat(id: UUID(), turns: [], createdAt: .now, updatedAt: .now)
    chat.turns.append(turn)
    self.chat = chat
    input = ""
    save()
    run(chat: chat.id, turn: turn.id, question: question)
  }

  func retry(_ turn: Turn) {
    guard let chat, !isStreaming else { return }
    update(chat.id, turn.id) {
      $0.answer = ""
      $0.citations = []
      $0.status = .streaming
    }
    run(chat: chat.id, turn: turn.id, question: turn.question)
  }

  func stop() {
    stream?.cancel()
    stream = nil
    guard var chat, chat.turns.contains(where: \.isStreaming) else { return }
    chat.settle()
    self.chat = chat
    save()
  }

  func newChat() {
    stop()
    chat = nil
  }

  /// Opens a chat from History. If it can't be fetched, the open chat stays.
  func open(_ id: UUID) async throws {
    guard id != chat?.id else { return }
    // Pending saves first, so the server copy has the latest turns; a copy
    // the server never got is newer than anything it has.
    await afterWrites {}.value
    var opened: Chat
    if let local = unsaved[id] {
      opened = local
    } else {
      opened = try await APIClient.shared.chat(id)
    }
    opened.settle()
    stop()
    chat = opened
  }

  func delete(_ id: UUID) async {
    if id == chat?.id { newChat() }
    history?.removeAll { $0.id == id }
    let deleted = await afterWrites { [weak self] () async -> Bool in
      do {
        try await APIClient.shared.deleteChat(id)
      } catch APIError.http(404, _) {
        // Never reached the server.
      } catch {
        return false
      }
      self?.unsaved[id] = nil
      return true
    }.value
    if deleted {
      Haptics.success()
    } else {
      Haptics.error()
      await loadHistory()
    }
  }

  private func run(chat: UUID, turn: UUID, question: String) {
    stream?.cancel()
    stream = Task {
      do {
        for try await event in APIClient.shared.askStream(question) {
          // Stop already saved the answer; tokens still buffered must not change it.
          if Task.isCancelled { break }
          switch event {
          case .token(let text): update(chat, turn) { $0.answer += text }
          case .done(let citations):
            update(chat, turn) {
              $0.citations = citations
              $0.status = .done
            }
          case .error(let message):
            update(chat, turn) { $0.status = Self.failure(APIError.http(500, message)) }
          }
        }
        update(chat, turn) { if $0.isStreaming { $0.status = .done } }
      } catch is CancellationError {
      } catch let error as URLError where error.code == .cancelled {
      } catch {
        update(chat, turn) { $0.status = Self.failure(error) }
      }
      guard !Task.isCancelled else { return }
      finished(chat, turn)
    }
  }

  /// A light tap when the answer lands, an error buzz when it fails.
  private func finished(_ chatId: UUID, _ turnId: UUID) {
    guard chat?.id == chatId, let turn = chat?.turns.first(where: { $0.id == turnId }) else { return }
    if case .failed = turn.status { Haptics.error() } else { Haptics.tap() }
    save()
  }

  private static func failure(_ error: Error) -> Turn.Status {
    let f = Format.friendlyAskError(error)
    return .failed(title: f.title, detail: f.detail)
  }

  private func update(_ chatId: UUID, _ turnId: UUID, _ change: (inout Turn) -> Void) {
    guard chat?.id == chatId, let t = chat?.turns.firstIndex(where: { $0.id == turnId }) else { return }
    change(&chat!.turns[t])
  }

  // MARK: Saving

  private func save() {
    guard var chat else { return }
    chat.updatedAt = .now
    self.chat = chat
    record(chat)
    upload(chat)
  }

  private func upload(_ snapshot: Chat) {
    let owner = SessionStore.shared.current()?.user.id
    afterWrites { [weak self] in
      // Signed out or switched accounts while this waited: never save into another account.
      guard SessionStore.shared.current()?.user.id == owner else { return }
      do {
        _ = try await APIClient.shared.saveChat(snapshot)
        self?.unsaved[snapshot.id] = nil
      } catch APIError.http(let status, _) where (400..<500).contains(status) {
        // Rejected (too long, say); sending the same copy again won't help.
        self?.unsaved[snapshot.id] = nil
      } catch {
        self?.unsaved[snapshot.id] = snapshot
      }
    }
  }

  /// Puts the chat at the top of History without waiting for the server.
  private func record(_ chat: Chat) {
    let summary = ChatSummary(
      id: chat.id, questions: chat.turns.map(\.question), createdAt: chat.createdAt, updatedAt: chat.updatedAt)
    history?.removeAll { $0.id == chat.id }
    history?.insert(summary, at: 0)
  }

  @discardableResult
  private func afterWrites<T: Sendable>(_ work: @escaping () async -> T) -> Task<T, Never> {
    let previous = writes
    let task = Task {
      await previous?.value
      return await work()
    }
    writes = Task { _ = await task.value }
    return task
  }
}
