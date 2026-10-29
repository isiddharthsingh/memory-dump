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

struct Session: Codable, Equatable {
  let accessToken: String
  let refreshToken: String
  let user: User
}

struct TokenResponse: Codable {
  let accessToken: String
  let refreshToken: String
  let user: User

  var session: Session { Session(accessToken: accessToken, refreshToken: refreshToken, user: user) }
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
