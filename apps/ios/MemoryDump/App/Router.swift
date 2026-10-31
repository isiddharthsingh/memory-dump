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
  /// cite/wiki:<slug> opens a source. Anything else goes to the system.
  func open(_ url: URL) -> OpenURLAction.Result {
    let s = url.absoluteString
    if s.hasPrefix(LinkRewriter.wikiPrefix) {
      push(.wiki(String(s.dropFirst(LinkRewriter.wikiPrefix.count))))
      return .handled
    }
    if s.hasPrefix(LinkRewriter.citePrefix) {
      let key = s.dropFirst(LinkRewriter.citePrefix.count)
      if key.hasPrefix("wiki:") {
        push(.wiki(String(key.dropFirst(5))))
      } else if let id = UUID(uuidString: String(key.hasPrefix("item:") ? key.dropFirst(5) : key)) {
        push(.item(id))
      }
      return .handled
    }
    return .systemAction
  }
}
