import Foundation

// Response types for the backend API. Field names arrive in snake_case and are
// converted by `JSONDecoder.api`. Each type mirrors a schema in
// backend/app/api/schemas.py; unknown fields are ignored.

struct ItemSummary: Codable, Identifiable, Hashable {
  let id: UUID
  let url: String
  let source: String
  let status: String
  let title: String?
  let summary: String?
  let thumbnailUrl: String?
  let tags: [String]?
  // Optional so the app still parses servers older than this field.
  let error: String?
  let createdAt: Date
  /// What the save is for: place, recipe, product, media, howto or other.
  /// Nil for items analysed before kinds, and from servers older than them.
  var kind: String? = nil
  /// When it was marked done; nil while it isn't.
  var doneAt: Date? = nil

  /// The statuses of a save the server hasn't finished with: waiting, then running.
  static let busyStatuses = ["pending", "processing"]

  var isBusy: Bool { Self.busyStatuses.contains(status) }
  var isFailed: Bool { status == "failed" }
  var isDone: Bool { doneAt != nil }
}

struct ItemDetail: Codable, Identifiable, Hashable {
  let id: UUID
  let url: String
  let source: String
  let status: String
  let title: String?
  let summary: String?
  let thumbnailUrl: String?
  let tags: [String]?
  let error: String?
  let createdAt: Date
  let rawText: String?
  let people: [String]?
  let handles: [String]?
  let brandsProducts: [String]?
  let topics: [String]?
  let shareText: String?
  var kind: String? = nil
  var doneAt: Date? = nil
  var details: ItemDetails? = nil

  var isBusy: Bool { ItemSummary.busyStatuses.contains(status) }
  var isDone: Bool { doneAt != nil }
}

/// What an item's kind keeps: a place's addresses, a recipe's ingredients and
/// steps, a how-to's steps, a product's price.
struct ItemDetails: Codable, Hashable {
  struct Place: Codable, Hashable {
    let name: String
    let address: String

    /// What a map is asked for: the name, and the address when there is one.
    var query: String { address.isEmpty ? name : "\(name), \(address)" }
  }

  var places: [Place]? = nil
  var ingredients: [String]? = nil
  var steps: [String]? = nil
  var price: String? = nil
}

/// One place a saved item names, for the map (`/items/places`).
struct SavedPlace: Codable, Hashable, Identifiable {
  let item: UUID
  let title: String?
  let name: String
  let address: String
  let done: Bool

  var id: String { "\(item.lower)|\(name)|\(address)" }
  var query: String { ItemDetails.Place(name: name, address: address).query }
}

struct TopicPageSummary: Codable, Identifiable, Hashable {
  let id: UUID
  let slug: String
  let title: String
  let backlinks: [String]?
  let sourceItemIds: [UUID]?
  let version: Int
  let updatedAt: Date
}

struct TopicPageDetail: Codable, Identifiable, Hashable {
  let id: UUID
  let slug: String
  let title: String
  let backlinks: [String]?
  let sourceItemIds: [UUID]?
  let version: Int
  let updatedAt: Date
  let bodyMd: String
  /// What the user said the page gets wrong, oldest first.
  var corrections: [Correction]? = nil
}

struct Correction: Codable, Hashable, Identifiable {
  let id: UUID
  let text: String
  let at: Date
}

struct SearchHit: Codable, Hashable {
  let kind: String  // "item" | "topic_page"
  let id: UUID
  let title: String?
  let url: String?
  let slug: String?
  let snippet: String
  let score: Double
}

struct Citation: Codable, Hashable {
  let kind: String  // "item" | "topic_page"
  let id: UUID
  let title: String?
  let url: String?
  let slug: String?

  /// The key the answer text uses for this source: "item:<id>" or "wiki:<slug>".
  var key: String { kind == "item" ? "item:\(id.lower)" : "wiki:\(slug ?? id.lower)" }
}

/// One question in an Ask chat and its answer. The JSON keys are single
/// words, since request bodies are encoded without snake_case conversion.
struct ChatTurn: Codable, Identifiable {
  enum Status {
    case streaming
    case done
    case failed(title: String, detail: String)
  }

  let id: UUID
  let question: String
  var answer = ""
  var citations: [Citation] = []
  var status: Status = .streaming

  var isStreaming: Bool { if case .streaming = status { true } else { false } }

  private enum CodingKeys: String, CodingKey { case id, question, answer, citations, status, error }
  private struct Failure: Codable {
    let title: String
    let detail: String
  }
}

extension ChatTurn {
  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = try c.decode(UUID.self, forKey: .id)
    question = try c.decode(String.self, forKey: .question)
    answer = try c.decodeIfPresent(String.self, forKey: .answer) ?? ""
    citations = try c.decodeIfPresent([Citation].self, forKey: .citations) ?? []
    switch try c.decode(String.self, forKey: .status) {
    case "streaming": status = .streaming
    case "failed":
      let f = try c.decodeIfPresent(Failure.self, forKey: .error)
      status = .failed(title: f?.title ?? "Couldn’t answer", detail: f?.detail ?? "")
    default: status = .done
    }
  }

  func encode(to encoder: Encoder) throws {
    var c = encoder.container(keyedBy: CodingKeys.self)
    try c.encode(id, forKey: .id)
    try c.encode(question, forKey: .question)
    try c.encode(answer, forKey: .answer)
    try c.encode(citations, forKey: .citations)
    switch status {
    case .streaming: try c.encode("streaming", forKey: .status)
    case .done: try c.encode("done", forKey: .status)
    case .failed(let title, let detail):
      try c.encode("failed", forKey: .status)
      try c.encode(Failure(title: title, detail: detail), forKey: .error)
    }
  }
}

/// One Ask conversation, saved on the server so it follows the account.
struct Chat: Codable, Identifiable {
  let id: UUID
  var turns: [ChatTurn]
  var createdAt: Date
  var updatedAt: Date
  /// The server's version these turns are built on, sent back with the next
  /// save. Nil, sent as 0, for a new chat, and for copies saved on the phone
  /// before chats had versions (a save from one meets a 409 and merges).
  var version: Int?

  /// A turn still streaming was cut off (the app quit, or Stop). Keep what
  /// arrived; with nothing, fail it so it shows Try again.
  mutating func settle() {
    for i in turns.indices where turns[i].isStreaming {
      turns[i].status =
        turns[i].answer.isEmpty
        ? .failed(title: "No answer", detail: "This answer stopped before it started.") : .done
    }
  }

  /// Another device saved the chat since this one's copy: the server's turns
  /// in order, each replaced by the local turn with the same id, then the
  /// local turns the server doesn't have, in local order.
  static func merge(server: [ChatTurn], local: [ChatTurn]) -> [ChatTurn] {
    let mine = Dictionary(local.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
    let theirs = Set(server.map(\.id))
    return server.map { mine[$0.id] ?? $0 } + local.filter { !theirs.contains($0.id) }
  }
}

/// What an Ask chat is about when it's one thing ("Ask about this"): the
/// server answers from that saved item, or from that wiki page and the items
/// it cites, instead of searching the whole library. Held for the open chat
/// only; a chat reopened from History has none.
enum AskScope: Hashable {
  case item(id: UUID, title: String)
  case topic(slug: String, title: String)

  var title: String {
    switch self {
    case .item(_, let title), .topic(_, let title): title
    }
  }
}

/// The body of /ask/stream. The keys are single words, since request bodies
/// are encoded with a plain JSONEncoder. A nil `item` or `topic` is left out.
struct AskBody: Encodable {
  struct Exchange: Encodable, Equatable {
    let question: String
    let answer: String
  }

  /// The server reads at most this many earlier exchanges.
  static let historyLimit = 6

  let question: String
  /// Earlier answered questions of the chat, oldest first, so a follow-up
  /// ("what did the second one say?") is read with them.
  let history: [Exchange]
  let item: String?
  let topic: String?
  /// The phone's time zone (an IANA name), whose days "today" and "what did I
  /// save yesterday" mean; the server works in UTC without it.
  let zone: String

  /// `earlier` is the chat's turns before this question. Only finished ones
  /// with an answer go, the last `historyLimit` of them: a failed turn has
  /// nothing to follow up on.
  init(question: String, earlier: [ChatTurn], scope: AskScope?, zone: TimeZone = .current) {
    self.question = question
    self.zone = zone.identifier
    history = earlier
      .filter { if case .done = $0.status { !$0.answer.isEmpty } else { false } }
      .suffix(Self.historyLimit)
      .map { Exchange(question: $0.question, answer: $0.answer) }
    switch scope {
    case .item(let id, _):
      item = id.lower
      topic = nil
    case .topic(let slug, _):
      item = nil
      topic = slug
    case nil:
      item = nil
      topic = nil
    }
  }
}

/// A chat in History: just its questions, so the list doesn't download every answer.
struct ChatSummary: Codable, Identifiable {
  let id: UUID
  var questions: [String]
  let createdAt: Date
  var updatedAt: Date
  let version: Int

  var title: String { questions.first ?? "New chat" }
}

/// What changed in the Library since the `until` of the last call.
struct ItemChanges: Codable {
  /// The server can't say what changed since `since`: walk the whole list.
  let full: Bool
  let changed: [UUID]
  let deleted: [UUID]
  /// A UTC timestamp, kept as the server wrote it and sent back as `since`.
  let until: String
}

/// Whose models work for this account and what they have cost this month
/// (`/ai`): Memory Dump's own, free up to a monthly amount, or a provider the
/// user saved their own API key for. Every write to it answers with all of it.
struct AISettings: Codable, Hashable {
  /// Spend on Memory Dump's own key this calendar month, in USD.
  struct Free: Codable, Hashable {
    /// Nil for an admin, who has no cap.
    let limit: Double?
    let spent: Double
    /// The day the month rolls over, "yyyy-MM-dd".
    let resets: String
  }

  /// Spend on the user's own keys this calendar month, in USD.
  struct Own: Codable, Hashable {
    /// The monthly limit the user set to protect their key; nil for none.
    var limit: Double?
    let spent: Double
  }

  struct Provider: Codable, Hashable, Identifiable {
    /// A saved key. The key itself never comes back, only its last 4 characters.
    struct Key: Codable, Hashable {
      let hint: String
      /// The AWS region, for a provider that needs one.
      let region: String?
      let added: Date
    }

    struct Model: Codable, Hashable, Identifiable {
      let id: String
      let name: String
    }

    /// The model in use for each of the three jobs.
    struct Chosen: Codable, Hashable {
      var analysis: String
      var synthesis: String
      var ask: String
    }

    let id: String
    let name: String
    let placeholder: String
    /// Where to get a key, in a sentence.
    let help: String
    /// Its key form also asks for an AWS region.
    let region: Bool
    let key: Key?
    let models: [Model]
    var chosen: Chosen
  }

  /// Voyage indexes and searches the library, whichever provider's models
  /// the account uses, so its key is saved beside a provider's.
  struct Voyage: Codable, Hashable {
    /// What the `/ai/keys` routes call it.
    let id: String
    let name: String
    let placeholder: String
    let help: String
    /// The user's own Voyage key, which makes their Voyage calls when saved.
    let key: Provider.Key?
    /// What the account may use of Memory Dump's Voyage key this month, when
    /// that is capped on its own (on the user's own models, with no Voyage
    /// key); nil when it isn't.
    let limit: Double?
    let spent: Double
  }

  /// No cap on Memory Dump's own models for this account.
  let admin: Bool
  /// False when the server isn't set up to store keys.
  let keys: Bool
  /// The provider whose saved key is in use; nil for Memory Dump's own models.
  var provider: String?
  let free: Free
  var own: Own
  /// Nil in a copy saved before the server said anything of Voyage.
  var voyage: Voyage? = nil
  var providers: [Provider]

  /// The bodies of the two `/ai` writes whose value can be nil. It goes out
  /// as null, which a synthesized encoder would leave out: the server reads
  /// null as "Memory Dump's own models" and "no limit".
  struct ProviderBody: Encodable {
    let provider: String?

    func encode(to encoder: Encoder) throws {
      var c = encoder.container(keyedBy: CodingKeys.self)
      try c.encode(provider, forKey: .provider)
    }

    private enum CodingKeys: String, CodingKey { case provider }
  }

  struct LimitBody: Encodable {
    let limit: Double?

    func encode(to encoder: Encoder) throws {
      var c = encoder.container(keyedBy: CodingKeys.self)
      try c.encode(limit, forKey: .limit)
    }

    private enum CodingKeys: String, CodingKey { case limit }
  }
}

struct TagCount: Codable, Hashable {
  let tag: String
  let count: Int
}

struct User: Codable, Hashable {
  let id: UUID
  let email: String
  let displayName: String?
  /// `+` and digits, country code first. Nil for an account made before sign-up
  /// asked for it, and in a session saved before the server sent it.
  var phone: String? = nil
  let createdAt: Date
}

/// A SuperTokens session: the tokens from the `st-access-token` and
/// `st-refresh-token` response headers, and the `/auth/me` profile.
struct Session: Codable, Equatable {
  let accessToken: String
  let refreshToken: String
  let user: User
}

/// The body of SuperTokens' sign-in and sign-up responses. A refusal is still
/// a 200, with a status instead of "OK". Its keys are camelCase, so it is read
/// with a plain JSONDecoder.
struct AuthResult: Decodable {
  struct FieldError: Decodable {
    let id: String
    let error: String
  }

  let status: String
  let formFields: [FieldError]?
  let message: String?
  let reason: String?

  /// What to tell the person, or nil when it worked.
  var problem: String? {
    switch status {
    case "OK": nil
    case "WRONG_CREDENTIALS_ERROR": "Incorrect email or password."
    case "FIELD_ERROR": formFields?.first?.error ?? "Check your email and password."
    default: message ?? reason ?? "The server said \(status)."
    }
  }
}

struct IngestResponse: Codable { let item: ItemSummary }
struct SearchResponse: Codable { let hits: [SearchHit] }
struct TagsResponse: Codable { let tags: [TagCount] }
struct RelatedResponse: Codable {
  let inWiki: [TopicPageSummary]
  let related: [ItemSummary]
}
struct DeviceResponse: Codable { let id: String }

extension UUID {
  /// The id as the server writes it: lowercase.
  var lower: String { uuidString.lowercased() }
}

extension JSONDecoder {
  static var api: JSONDecoder {
    let d = JSONDecoder()
    d.keyDecodingStrategy = .convertFromSnakeCase
    d.dateDecodingStrategy = .custom { decoder in
      let raw = try decoder.singleValueContainer().decode(String.self)
      guard let date = ISODate.parse(raw) else {
        throw DecodingError.dataCorrupted(
          .init(codingPath: decoder.codingPath, debugDescription: "Bad date: \(raw)"))
      }
      return date
    }
    return d
  }
}

extension JSONEncoder {
  static var api: JSONEncoder {
    let e = JSONEncoder()
    e.keyEncodingStrategy = .convertToSnakeCase
    // Strings, so `JSONDecoder.api` reads back what this writes.
    e.dateEncodingStrategy = .iso8601
    return e
  }
}

/// Pydantic writes datetimes with microseconds ("…T19:54:13.728123Z"), with
/// no fraction on a whole second, and with no zone when the value has none
/// (UTC). The format styles are made once: a formatter made for each date was
/// nearly all of the time it took to decode a list.
/// Nonisolated: the decoder calls it from its own context.
nonisolated enum ISODate {
  private static let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
  private static let whole = Date.ISO8601FormatStyle()

  static func parse(_ raw: String) -> Date? {
    let zoned = raw.hasSuffix("Z") || raw.drop { $0 != "T" }.contains { $0 == "+" || $0 == "-" }
    let s = zoned ? raw : raw + "Z"
    return read(s, withFraction) ?? read(s, whole)
  }

  /// Nil unless the timestamp is all of the string: `parse` on a style stops
  /// reading where the timestamp ends and takes whatever follows it.
  private static func read(_ s: String, _ style: Date.ISO8601FormatStyle) -> Date? {
    guard let (end, date) = try? style.consuming(s, startingAt: s.startIndex, in: s.startIndex..<s.endIndex),
      end == s.endIndex
    else { return nil }
    return date
  }
}
