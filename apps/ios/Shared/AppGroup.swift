import Foundation

/// Storage the app and the share extension both reach. Both targets carry the
/// same App Group and Keychain group entitlements (see project.yml).
enum AppGroup {
  static let identifier = "group.com.siddharthsingh.memorydump"

  /// "<team prefix>com.siddharthsingh.memorydump.shared". Read from Info.plist so
  /// the team prefix comes from the build settings.
  static let keychainAccessGroup: String? = {
    guard let group = Bundle.main.object(forInfoDictionaryKey: "KeychainAccessGroup") as? String,
      !group.isEmpty
    else { return nil }
    return group
  }()

  /// The App Group container. Falls back to this process's Application Support
  /// when the entitlement is missing, so the app still works on its own.
  static let container: URL =
    FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier)
    ?? URL.applicationSupportDirectory

  static func directory(_ name: String) -> URL {
    let url = container.appending(path: name, directoryHint: .isDirectory)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
  }

  /// Runs `body` while holding an exclusive flock() on a file in the container.
  /// The lock belongs to the open file, so it also serializes callers inside one
  /// process, and closing the file releases it.
  static func withFileLock<T>(_ name: String, _ body: () async throws -> T) async throws -> T {
    let path = container.appending(path: name).path(percentEncoded: false)
    let fd = open(path, O_CREAT | O_RDWR, 0o600)
    guard fd >= 0 else { return try await body() }
    defer { close(fd) }
    while flock(fd, LOCK_EX | LOCK_NB) != 0 {
      try await Task.sleep(for: .milliseconds(50))
    }
    return try await body()
  }
}
