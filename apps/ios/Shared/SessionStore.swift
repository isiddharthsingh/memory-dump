import Foundation

/// The signed-in session, kept in the shared Keychain so the share extension
/// uses the same one as the app. Every read goes to the Keychain, because the
/// other process can rotate the tokens at any time.
final class SessionStore {
  static let shared = SessionStore()

  /// Called after `expire()` clears the session. The app shows sign-in.
  var onSignedOut: (() -> Void)?

  private enum Key {
    static let session = "session"
  }

  func current() -> Session? {
    Keychain.data(Key.session).flatMap { try? JSONDecoder.api.decode(Session.self, from: $0) }
  }

  func save(_ session: Session) {
    guard let data = try? JSONEncoder.api.encode(session) else { return }
    Keychain.set(data, for: Key.session)
  }

  func clear() {
    Keychain.delete(Key.session)
  }

  /// Drops a session the server no longer accepts. The app shows sign-in.
  func expire() {
    clear()
    onSignedOut?()
  }

  /// One refresh at a time across the app and the extension. SuperTokens
  /// rotates the refresh token and ends the session when a replaced one is
  /// used again, so a second caller must pick up the tokens the first one
  /// wrote instead of refreshing again.
  func refresh(after stale: Session) async throws -> Session {
    try await AppGroup.withFileLock("session.lock") {
      guard let now = current() else { throw APIError.signedOut }
      // The other process (or another request here) refreshed first.
      if now.refreshToken != stale.refreshToken { return now }
      do {
        let fresh = try await AuthAPI.refresh(session: now)
        save(fresh)
        return fresh
      } catch APIError.http(401, _) {
        expire()
        throw APIError.signedOut
      }
    }
  }
}
