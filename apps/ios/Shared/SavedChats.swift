import Foundation

extension APIClient {
  /// A page of History as `chats()` asks for it, which is also what its copy
  /// is saved under.
  static func chatsQuery(limit: Int = chatPageSize, before: UUID? = nil) -> [String: String?] {
    ["limit": String(limit), "before": before?.lower]
  }
}

/// The chat copies the phone writes itself, beside the ones GETs save.
extension SavedResponses {
  /// A save the server took. Its answer is only a summary, so the chat is
  /// written here as the copy of its GET and put at the top of History's, and
  /// both can be read offline before the next sync.
  func keep(_ chat: Chat, as saved: ChatSummary, user: UUID, pageSize: Int = APIClient.chatPageSize) {
    var copy = chat
    copy.createdAt = saved.createdAt
    copy.updatedAt = saved.updatedAt
    copy.version = saved.version
    guard let data = try? JSONEncoder.api.encode(copy) else { return }
    write(data, "/chats/\(chat.id.lower)", user: user)

    // One chat isn't the whole of History: with no list saved, none is made up.
    // The other rows move as the server wrote them, not decoded and written
    // again, which would cut their dates to the second.
    let key = APIClient.savedKey("/chats", APIClient.chatsQuery(limit: pageSize))
    guard let page = read(key, user: user).flatMap({ try? JSONSerialization.jsonObject(with: $0) }) as? [[String: Any]],
      let row = (try? JSONEncoder.api.encode(saved)).flatMap({ try? JSONSerialization.jsonObject(with: $0) })
        as? [String: Any]
    else { return }
    // The server writes ids in lower case and JSONEncoder in upper.
    func id(_ row: [String: Any]?) -> String? { (row?["id"] as? String)?.lowercased() }
    let rows = [row] + page.filter { id($0) != saved.id.lower }
    // A full page says there's a next one, saved under this page's last row:
    // the list is left alone when the row would fill it or move its last row.
    guard rows.count < pageSize || (rows.count == page.count && id(rows.last) == id(page.last)),
      let list = try? JSONSerialization.data(withJSONObject: rows)
    else { return }
    write(list, key, user: user)
  }

  /// The version of the chat's saved copy, read without decoding its turns.
  func chatVersion(_ id: UUID, user: UUID) -> Int? {
    struct Versioned: Decodable { let version: Int? }
    return read("/chats/\(id.lower)", user: user)
      .flatMap { try? JSONDecoder().decode(Versioned.self, from: $0) }?.version
  }
}

extension Chat {
  /// Whether this copy is the chat at the version the server lists, so it
  /// can be read without asking for it. Any other version is fetched: a
  /// higher one listed is another device's save, and a lower one a chat that
  /// was deleted and saved again, which starts its versions over.
  func isCurrent(for listed: ChatSummary) -> Bool {
    id == listed.id && version == listed.version
  }
}
