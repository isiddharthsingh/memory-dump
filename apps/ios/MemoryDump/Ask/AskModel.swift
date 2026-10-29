import Foundation

@Observable
final class AskModel {
  struct Turn: Identifiable {
    enum Status {
      case streaming
      case done
      case failed(title: String, detail: String)
    }

    let id: Int
    let question: String
    var answer = ""
    var citations: [Citation] = []
    var status: Status = .streaming

    var isStreaming: Bool { if case .streaming = status { true } else { false } }
  }

  private(set) var turns: [Turn] = []
  var input = ""
  private(set) var tags: [TagCount] = []
  private(set) var wiki: [TopicPageSummary] = []

  private var stream: Task<Void, Never>?
  private var nextId = 1

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

  func submit(_ raw: String) {
    let question = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !question.isEmpty, !isStreaming else { return }
    let id = nextId
    nextId += 1
    turns.append(Turn(id: id, question: question))
    input = ""
    run(id: id, question: question)
  }

  func retry(_ turn: Turn) {
    update(turn.id) {
      $0.answer = ""
      $0.citations = []
      $0.status = .streaming
    }
    run(id: turn.id, question: turn.question)
  }

  func stop() {
    stream?.cancel()
    stream = nil
    for i in turns.indices where turns[i].isStreaming { turns[i].status = .done }
  }

  func startOver() {
    stop()
    turns = []
  }

  private func run(id: Int, question: String) {
    stream?.cancel()
    stream = Task {
      do {
        for try await event in APIClient.shared.askStream(question) {
          switch event {
          case .token(let text): update(id) { $0.answer += text }
          case .done(let citations):
            update(id) {
              $0.citations = citations
              $0.status = .done
            }
          case .error(let message):
            update(id) { $0.status = Self.failure(APIError.http(500, message)) }
          }
        }
        update(id) { if $0.isStreaming { $0.status = .done } }
      } catch is CancellationError {
      } catch let error as URLError where error.code == .cancelled {
      } catch {
        update(id) { $0.status = Self.failure(error) }
      }
    }
  }

  private static func failure(_ error: Error) -> Turn.Status {
    let f = Format.friendlyAskError(error)
    return .failed(title: f.title, detail: f.detail)
  }

  private func update(_ id: Int, _ change: (inout Turn) -> Void) {
    guard let i = turns.firstIndex(where: { $0.id == id }) else { return }
    change(&turns[i])
  }
}
