import SwiftUI

enum AppTab: Hashable {
  case library
  case wiki
  case ask
}

enum Route: Hashable {
  case item(UUID)
  case wiki(String)
}

/// Tab selection, one navigation path per tab, and the app's own link scheme.
@Observable
final class Router {
  var tab: AppTab = .library
  var libraryPath: [Route] = []
  var wikiPath: [Route] = []
  var askPath: [Route] = []
  /// A question to put in the Ask box ("Ask about this").
  var askDraft: String?
  /// What that question is about, for the new chat it starts.
  var askScope: AskScope?

  /// Every item, topic and source opens through here, so this is where they tap.
  func push(_ route: Route) {
    Haptics.tap()
    switch tab {
    case .library: libraryPath.append(route)
    case .wiki: wikiPath.append(route)
    case .ask: askPath.append(route)
    }
  }

  func ask(_ question: String, scope: AskScope? = nil) {
    askScope = scope
    askDraft = question
    tab = .ask
  }

  /// memorydump://wiki/<slug> opens a topic; memorydump://cite/item:<id> or
  /// cite/wiki:<slug> opens a source. Web and mail links go to the system.
  /// Anything else is dropped: wiki pages and answers are written by a model
  /// from saved pages, so a link in them could name any app or file.
  func open(_ url: URL) -> OpenURLAction.Result {
    let s = url.absoluteString
    if s.hasPrefix(LinkRewriter.wikiPrefix) {
      openTopic(String(s.dropFirst(LinkRewriter.wikiPrefix.count)))
      return .handled
    }
    if s.hasPrefix(LinkRewriter.citePrefix) {
      let key = s.dropFirst(LinkRewriter.citePrefix.count)
      if key.hasPrefix("wiki:") {
        openTopic(String(key.dropFirst(5)))
      } else if let id = UUID(uuidString: String(key.hasPrefix("item:") ? key.dropFirst(5) : key)) {
        push(.item(id))
      }
      return .handled
    }
    return Self.goesToSystem(url) ? .systemAction : .discarded
  }

  /// Whether a link that isn't the app's own is one the system may open.
  static func goesToSystem(_ url: URL) -> Bool {
    ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "")
  }

  /// Whether this is a slug the server could have made: python-slugify's
  /// lowercase letters, digits and hyphens, as the `[[slug]]` pattern in
  /// backend/app/ai/synthesize.py reads them, and no longer than the column.
  /// Anything else would be a path of its own once it's in the page's URL.
  static func isSlug(_ slug: String) -> Bool {
    slug.wholeMatch(of: /[a-z0-9][a-z0-9-]{0,254}/) != nil
  }

  private func openTopic(_ slug: String) {
    guard Self.isSlug(slug) else { return }
    push(.wiki(slug))
  }
}

/// The pages the server publishes for anyone to read, outside the API.
/// Opened with the system, never through APIClient.
enum PublicPage {
  static var privacy: URL { Server.url.appending(path: "privacy") }
  static var terms: URL { Server.url.appending(path: "terms") }
  static var support: URL { Server.url.appending(path: "support") }
}
