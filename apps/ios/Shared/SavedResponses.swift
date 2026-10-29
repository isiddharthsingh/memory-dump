import CryptoKit
import Foundation

/// The last body each GET returned, kept on disk per account, so a screen can
/// show it at once and fall back to it when the server can't be reached (see
/// APIClient). Lives in Application Support, which the system doesn't purge,
/// and stays out of backups because the server has all of it.
struct SavedResponses {
  static let shared = SavedResponses(
    root: URL.applicationSupportDirectory.appending(path: "Offline", directoryHint: .isDirectory))

  let root: URL

  func read(_ key: String, user: UUID) -> Data? {
    try? Data(contentsOf: file(key, user))
  }

  func write(_ data: Data, _ key: String, user: UUID) {
    let dir = directory(user)
    if !FileManager.default.fileExists(atPath: dir.path(percentEncoded: false)) {
      try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
      var root = root
      var values = URLResourceValues()
      values.isExcludedFromBackup = true
      try? root.setResourceValues(values)
    }
    try? data.write(to: file(key, user), options: .atomic)
  }

  func remove(_ key: String, user: UUID) {
    try? FileManager.default.removeItem(at: file(key, user))
  }

  /// Every account's copies, for sign-out.
  func clear() {
    try? FileManager.default.removeItem(at: root)
  }

  private func directory(_ user: UUID) -> URL {
    root.appending(path: user.lower, directoryHint: .isDirectory)
  }

  /// Keys are paths with queries, so they're hashed into file names.
  private func file(_ key: String, _ user: UUID) -> URL {
    let name = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
    return directory(user).appending(path: "\(name).json")
  }
}
