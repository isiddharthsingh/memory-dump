import SwiftUI
import UserNotifications

/// App-wide state: the session, the toast, and the background chores that run
/// when the app becomes active (send the share queue, register for push).
@Observable
final class AppModel {
  struct Toast: Equatable {
    let message: String
    let ok: Bool
  }

  private(set) var session: Session?
  var toast: Toast?
  let router = Router()
  /// Bumped each time the app becomes active, so the Library reloads and shows
  /// what the share extension saved while the app was in the background.
  private(set) var libraryVersion = 0

  private let store = SessionStore.shared
  private var toastTask: Task<Void, Never>?

  init() {
    session = store.current()
    store.onSignedOut = { [weak self] in
      Self.forgetSavedCopies()
      self?.session = nil
    }
  }

  // MARK: Session

  func signIn(email: String, password: String) async throws {
    finishSignIn(try await AuthAPI.signIn(email: email, password: password))
  }

  func signUp(email: String, password: String, displayName: String?) async throws {
    finishSignIn(try await AuthAPI.signUp(email: email, password: password, displayName: displayName))
  }

  func signOut() async {
    // Best effort: the local session is cleared either way.
    if session != nil { try? await APIClient.shared.signOut() }
    store.clear()
    Self.forgetSavedCopies()
    session = nil
  }

  func deleteAccount() async throws {
    try await APIClient.shared.deleteAccount()
    store.clear()
    Self.forgetSavedCopies()
    session = nil
  }

  private func finishSignIn(_ session: Session) {
    Haptics.success()
    store.save(session)
    self.session = session
    Task { await registerForPush() }
    Task { await OfflineSync.run() }
  }

  /// Nothing from an account stays on the phone after it signs out.
  private static func forgetSavedCopies() {
    SavedResponses.shared.clear()
    ImageCache.clear()
  }

  // MARK: Becoming active

  func becameActive() async {
    // The extension can rotate the tokens while the app is in the background.
    if session != nil {
      session = store.current()
      // Or ended it, when the server refused its refresh there.
      if session == nil { Self.forgetSavedCopies() }
    }
    guard session != nil else { return }
    let result = await ShareQueue.flush()
    libraryVersion += 1
    if result.sent > 0 {
      show("Sent \(Format.plural(result.sent, "queued share")).", ok: true)
    }
    await OfflineSync.run()
  }

  /// Asks for permission, then registers. The token arrives in AppDelegate.
  func registerForPush() async {
    let center = UNUserNotificationCenter.current()
    let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
    if granted { UIApplication.shared.registerForRemoteNotifications() }
  }

  func didReceivePushToken(_ token: Data) {
    guard session != nil else { return }
    let hex = token.map { String(format: "%02x", $0) }.joined()
    #if DEBUG
      let env = "sandbox"  // Xcode builds talk to the APNs sandbox.
    #else
      let env = "production"
    #endif
    Task { try? await APIClient.shared.registerDevice(token: hex, apnsEnv: env) }
  }

  // MARK: Toast

  func show(_ message: String, ok: Bool) {
    ok ? Haptics.success() : Haptics.error()
    toast = Toast(message: message, ok: ok)
    toastTask?.cancel()
    toastTask = Task {
      try? await Task.sleep(for: .seconds(4))
      if !Task.isCancelled { toast = nil }
    }
  }
}
