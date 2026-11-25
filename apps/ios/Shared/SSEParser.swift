import Foundation

enum AskEvent: Equatable {
  case token(String)
  /// A lookup the answer is making on the way ("Listing your places").
  case step(String)
  case done([Citation])
  case error(String)
}

/// Reads `/ask/stream`. The server writes each event as one `event:` line and
/// one `data:` line of JSON (backend/app/api/ask.py `_sse`).
struct SSEParser {
  private var event = "message"

  /// Feed one line from `URLSession.AsyncBytes.lines`. That sequence drops
  /// blank lines, so the frame ends at its `data:` line, not at a blank line.
  mutating func feed(_ line: String) throws -> AskEvent? {
    if line.hasPrefix("event:") {
      event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
      return nil
    }
    guard line.hasPrefix("data:") else { return nil }
    defer { event = "message" }
    let json = Data(line.dropFirst(5).trimmingCharacters(in: .whitespaces).utf8)
    switch event {
    case "token":
      return .token(try JSONDecoder.api.decode(TokenData.self, from: json).text)
    case "step":
      // Only a status line: one that can't be read is skipped.
      return (try? JSONDecoder.api.decode(TokenData.self, from: json).text).map(AskEvent.step)
    case "done":
      // A bad citation list should not lose the answer.
      let citations = (try? JSONDecoder.api.decode(DoneData.self, from: json).citations) ?? []
      return .done(citations)
    case "error":
      // The message is written for people and shown as is.
      let message = (try? JSONDecoder.api.decode(ErrorData.self, from: json).message)
      return .error(message ?? "Something went wrong. Try again.")
    default:
      return nil
    }
  }

  private struct TokenData: Decodable { let text: String }
  private struct DoneData: Decodable { let citations: [Citation] }
  private struct ErrorData: Decodable { let message: String }
}
