import CryptoKit
import Foundation

/// The last body each GET returned, kept on disk per account, so a screen can
/// show it at once and fall back to it when the server can't be reached (see
/// APIClient). Lives in Application Support, which the system doesn't purge,
/// and stays out of backups because the server has all of it. A body the
/// server sent an ETag with keeps it beside it, for If-None-Match.
struct SavedResponses {
  static let shared = SavedResponses(
    root: URL.applicationSupportDirectory.appending(path: "Offline", directoryHint: .isDirectory))

  let root: URL

  func read(_ key: String, user: UUID) -> Data? {
    try? Data(contentsOf: file(key, user))
  }

  /// The ETag the saved body came with, if it came with one.
  func etag(_ key: String, user: UUID) -> String? {
    (try? String(contentsOf: file(key, user, extension: "etag"), encoding: .utf8))?.nilIfEmpty
  }

  func write(_ data: Data, _ key: String, user: UUID, etag: String? = nil) {
    let dir = directory(user)
    if !FileManager.default.fileExists(atPath: dir.path(percentEncoded: false)) {
      try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
      var root = root
      var values = URLResourceValues()
      values.isExcludedFromBackup = true
      try? root.setResourceValues(values)
    }
    // The old ETag goes first, so it never stands for a body it didn't come with.
    try? FileManager.default.removeItem(at: file(key, user, extension: "etag"))
    try? data.write(to: file(key, user), options: .atomic)
    if let etag { try? Data(etag.utf8).write(to: file(key, user, extension: "etag"), options: .atomic) }
  }

  func remove(_ key: String, user: UUID) {
    try? FileManager.default.removeItem(at: file(key, user))
    try? FileManager.default.removeItem(at: file(key, user, extension: "etag"))
  }

  /// Every account's copies, for sign-out.
  func clear() {
    try? FileManager.default.removeItem(at: root)
  }

  private func directory(_ user: UUID) -> URL {
    root.appending(path: user.lower, directoryHint: .isDirectory)
  }

  /// Keys are paths with queries, so they're hashed into file names.
  private func file(_ key: String, _ user: UUID, extension ext: String = "json") -> URL {
    let name = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
    return directory(user).appending(path: "\(name).\(ext)")
  }
}
