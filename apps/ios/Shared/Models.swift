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

  var isBusy: Bool { status == "pending" || status == "processing" }
  var isFailed: Bool { status == "failed" }
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

  var isBusy: Bool { status == "pending" || status == "processing" }
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

  /// A turn still streaming was cut off (the app quit, or Stop). Keep what
  /// arrived; with nothing, fail it so it shows Try again.
  mutating func settle() {
    for i in turns.indices where turns[i].isStreaming {
      turns[i].status =
        turns[i].answer.isEmpty
        ? .failed(title: "No answer", detail: "This answer stopped before it started.") : .done
    }
  }
}

/// A chat in History: just its questions, so the list doesn't download every answer.
struct ChatSummary: Codable, Identifiable {
  let id: UUID
  var questions: [String]
  let createdAt: Date
  var updatedAt: Date

  var title: String { questions.first ?? "New chat" }
}

struct TagCount: Codable, Hashable {
  let tag: String
  let count: Int
}

struct User: Codable, Hashable {
  let id: UUID
  let email: String
  let displayName: String?
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

/// Pydantic writes datetimes with microseconds ("…T19:54:13.728123Z"), which
/// ISO8601DateFormatter does not read. Cut the fraction to milliseconds first.
/// Nonisolated: the decoder calls it from its own context.
nonisolated enum ISODate {
  static func parse(_ raw: String) -> Date? {
    var s = raw.replacing(/(\.\d{3})\d+/) { $0.1 }
    if s.wholeMatch(of: /.*(Z|[+-]\d{2}:?\d{2})/) == nil { s += "Z" }
    let withFraction = ISO8601DateFormatter()
    withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = withFraction.date(from: s) { return d }
    return ISO8601DateFormatter().date(from: s)
  }
}
