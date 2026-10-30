import Foundation

/// Keeps the whole account readable offline, not just what has been opened.
/// Every GET saves its answer (see APIClient), so this walks the Library in
/// the pages it scrolls by, then fetches each item, wiki page and chat that
/// changed since the last walk. Runs when the app becomes active.
enum OfflineSync {
  /// What the last walk saved: each item's status, each page's version and
  /// each chat's last change. Stored with the account's saved copies.
  struct Manifest: Codable, Equatable {
    var items: [UUID: String] = [:]
    var wiki: [String: Int] = [:]
    var chats: [UUID: Date] = [:]
  }

  private static let manifestKey = "offline-sync"
  private static var isRunning = false

  static func run(using api: APIClient = .shared) async {
    guard !isRunning, let user = SessionStore.shared.current()?.user.id else { return }
    isRunning = true
    defer { isRunning = false }
    let saves = SavedResponses.shared
    var manifest =
      saves.read(manifestKey, user: user).flatMap { try? JSONDecoder().decode(Manifest.self, from: $0) }
      ?? Manifest()
    do {
      var items: [ItemSummary] = []
      while true {
        let page = try await live(api) {
          try await api.listItems(limit: LibraryModel.pageSize, offset: items.count)
        }
        items += page
        if page.count < LibraryModel.pageSize { break }
      }
      let ids = Set(items.map(\.id))
      // Deleted since the last walk, here or on another device: drop the copies too.
      for id in manifest.items.keys where !ids.contains(id) {
        saves.remove("/items/\(id.lower)", user: user)
        saves.remove("/items/\(id.lower)/related", user: user)
      }
      manifest.items = manifest.items.filter { ids.contains($0.key) }
      for item in items where manifest.items[item.id] != item.status {
        manifest.items[item.id] = try await fetch(api) { try await api.item(item.id).status }
      }

      let pages = try await live(api) { try await api.wiki() }
      let slugs = Set(pages.map(\.slug))
      // The list holds only the 100 most recently updated pages, so a page
      // missing from it may still exist: drop its copy only on a 404.
      for slug in manifest.wiki.keys where !slugs.contains(slug) {
        do {
          _ = try await live(api) { try await api.wikiPage(slug) }
        } catch APIError.http(404, _) {
          saves.remove("/wiki/\(slug)", user: user)
        } catch APIError.http {
        }
      }
      manifest.wiki = manifest.wiki.filter { slugs.contains($0.key) }
      for page in pages where manifest.wiki[page.slug] != page.version {
        manifest.wiki[page.slug] = try await fetch(api) { try await api.wikiPage(page.slug).version }
      }

      let chats = try await live(api) { try await api.chats() }
      let chatIds = Set(chats.map(\.id))
      for id in manifest.chats.keys where !chatIds.contains(id) {
        saves.remove("/chats/\(id.lower)", user: user)
      }
      manifest.chats = manifest.chats.filter { chatIds.contains($0.key) }
      for chat in chats where manifest.chats[chat.id] != chat.updatedAt {
        manifest.chats[chat.id] = try await fetch(api) { try await api.chat(chat.id).updatedAt }
      }
    } catch {
      // Offline or signed out: keep what got saved, and pick up from there next time.
    }
    // Not if the account signed out during the walk.
    guard SessionStore.shared.current()?.user.id == user,
      let data = try? JSONEncoder().encode(manifest)
    else { return }
    saves.write(data, manifestKey, user: user)
  }

  /// Runs one request. With the server out of reach, APIClient answers with
  /// the saved copy, which has nothing new to save, so that ends the walk.
  private static func live<T>(_ api: APIClient, _ work: () async throws -> T) async throws -> T {
    let value = try await work()
    if api.isOffline { throw URLError(.cannotConnectToHost) }
    return value
  }

  /// Nil when the server refused this one (deleted since the list, say) or
  /// sent something unreadable; the next walk tries again. Any other error,
  /// like losing the network, ends the walk.
  private static func fetch<T>(_ api: APIClient, _ work: () async throws -> T) async throws -> T? {
    do {
      return try await live(api, work)
    } catch let error where error.isUnreachable {
      throw error
    } catch APIError.http {
      return nil
    } catch is DecodingError {
      return nil
    }
  }
}
