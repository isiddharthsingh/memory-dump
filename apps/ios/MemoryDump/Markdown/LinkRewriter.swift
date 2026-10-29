import Foundation

/// Wiki pages and Ask answers carry custom tokens: `[[slug]]` wikilinks and
/// `[item:<id>]` / `[wiki:<slug>]` citations. These turn them into markdown
/// links with app URLs, which MarkdownView sends to the Router.
enum LinkRewriter {
  static let wikiPrefix = "memorydump://wiki/"
  static let citePrefix = "memorydump://cite/"

  // MARK: Wiki pages (ported from the Expo app's wiki/[slug].tsx)

  /// Item ids in the order the page first cites them, then any uncited sources.
  static func citationOrder(_ md: String, sourceIds: [UUID]) -> [String] {
    var order: [String] = []
    for m in md.matches(of: /\[item:([A-Za-z0-9-]{8,})\]/) where !order.contains(String(m.1)) {
      order.append(String(m.1))
    }
    for id in sourceIds.map(\.lower) where !order.contains(id) { order.append(id) }
    return order
  }

  /// Wikilinks show the topic's name; citations show a number that matches the
  /// Sources list. A leading "# <Title>" goes, because the screen shows it.
  static func wiki(_ md: String, title: String, order: [String], titles: [String: String]) -> String {
    var lines = md.drop { $0.isWhitespace }.split(separator: "\n", omittingEmptySubsequences: false)
    if let first = lines.first, first.hasPrefix("# "),
      Format.topicTitle(first.dropFirst(2).trimmingCharacters(in: .whitespaces)).lowercased() == title.lowercased()
    {
      lines.removeFirst()
    }
    return lines.joined(separator: "\n")
      .replacing(/\[\[([a-z0-9][a-z0-9-]*)\]\]/) { m in
        let slug = String(m.1)
        let label = Format.topicTitle(titles[slug] ?? slug).replacing(/[\[\]]/, with: "")
        return "[\(label)](\(wikiPrefix)\(slug))"
      }
      .replacing(/\s*\[item:([A-Za-z0-9-]{8,})\]/) { m in
        let n = (order.firstIndex(of: String(m.1).lowercased()) ?? -1) + 1
        return "[\u{202F}\(n)\u{202F}](\(citePrefix)item:\(m.1))"
      }
  }

  // MARK: Ask answers (ported from the Expo app's AskTurn.tsx)

  struct NumberedSource: Identifiable {
    let n: Int
    let citation: Citation
    var id: String { citation.key }
  }

  /// Numbers sources in the order the answer first cites them; uncited ones go last.
  static func numberSources(answer: String, citations: [Citation]) -> (order: [String: Int], sources: [NumberedSource]) {
    var order: [String: Int] = [:]
    var keys: [String] = []
    func add(_ key: String) {
      guard order[key] == nil else { return }
      keys.append(key)
      order[key] = keys.count
    }
    for m in answer.matches(of: /\s*\[(item|wiki):([^\]]+)\]/) { add("\(m.1):\(m.2.lowercased())") }
    citations.forEach { add($0.key) }
    let byKey = Dictionary(citations.map { ($0.key, $0) }, uniquingKeysWith: { a, _ in a })
    let sources = keys.compactMap { key in byKey[key].map { NumberedSource(n: order[key]!, citation: $0) } }
    return (order, sources)
  }

  /// Answers are markdown; `[item:…]` / `[wiki:…]` markers become numbered links.
  static func answer(_ text: String, order: [String: Int]) -> String {
    text.replacing(/\s*\[(item|wiki):([^\]]+)\]/) { m in
      let key = "\(m.1):\(m.2.lowercased())"
      guard let n = order[key] else { return "" }
      return " [\u{202F}\(n)\u{202F}](\(citePrefix)\(key))"
    }
  }
}
