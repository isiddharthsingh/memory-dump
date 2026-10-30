import Network
import SwiftUI
import UserNotifications

/// App-wide state: the session, the toast, the share queue, and the chores
/// that run when the app becomes active (send the queue, register for push).
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
  /// Shares the server hasn't taken yet, oldest first.
  private(set) var queued: [QueuedShare] = []
  private(set) var isSendingQueue = false

  private let store = SessionStore.shared
  private var toastTask: Task<Void, Never>?
  private var queueTask: Task<Void, Never>?
  private var isActive = false
  private let network = NWPathMonitor()

  init() {
    session = store.current()
    store.onSignedOut = { [weak self] in
      Self.forgetSavedCopies()
      self?.session = nil
      self?.stopSendingQueue()
    }
    // Back on a network: try the queue now rather than at the next retry.
    network.pathUpdateHandler = Self.onConnected { [weak self] in
      Task { @MainActor in self?.sendQueue() }
    }
    network.start(queue: .global(qos: .utility))
  }

  /// Built outside the main actor: NWPathMonitor calls it on its own queue.
  private nonisolated static func onConnected(_ action: @escaping @Sendable () -> Void)
    -> @Sendable (NWPath) -> Void
  {
    { path in if path.status == .satisfied { action() } }
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
    stopSendingQueue()
    store.clear()
    Self.forgetSavedCopies()
    session = nil
  }

  func deleteAccount() async throws {
    try await APIClient.shared.deleteAccount()
    stopSendingQueue()
    store.clear()
    Self.forgetSavedCopies()
    session = nil
  }

  private func finishSignIn(_ session: Session) {
    Haptics.success()
    store.save(session)
    self.session = session
    sendQueue()
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
    isActive = true
    // The extension can rotate the tokens while the app is in the background.
    if session != nil {
      session = store.current()
      // Or ended it, when the server refused its refresh there.
      if session == nil { Self.forgetSavedCopies() }
    }
    guard session != nil else { return }
    sendQueue()
    libraryVersion += 1
    await OfflineSync.run()
  }

  func wentToBackground() {
    isActive = false
    // A send under way finishes, and the loop stops after it: cutting it off
    // could leave the server with a share the queue still holds.
    if !isSendingQueue { stopSendingQueue() }
  }

  // MARK: Share queue

  /// Sends the queue now, then again after 5, 10 and 20 s and every 30 s after
  /// that, for as long as anything is left, someone is signed in and the app is
  /// in front. Called again (a new network, Retry) while it waits, it tries at
  /// once; a send under way carries on.
  func sendQueue() {
    queued = ShareQueue.pending()
    guard isActive, session != nil, !queued.isEmpty, !isSendingQueue else { return }
    queueTask?.cancel()
    queueTask = Task {
      var pause = 5
      while true {
        isSendingQueue = true
        let result = await ShareQueue.flush()
        guard !Task.isCancelled else { return }
        isSendingQueue = false
        queued = ShareQueue.pending()
        if result.sent > 0 {
          libraryVersion += 1
          show("Sent \(Format.plural(result.sent, "queued share")).", ok: true)
        }
        if result.refused > 0 {
          show("\(Format.plural(result.refused, "share")) couldn’t be sent and were removed.", ok: false)
        }
        if queued.isEmpty || session == nil || !isActive { return }
        try? await Task.sleep(for: .seconds(pause))
        guard !Task.isCancelled else { return }
        pause = min(pause * 2, 30)
      }
    }
  }

  func discardQueue() {
    stopSendingQueue()
    ShareQueue.clear()
    queued = []
  }

  private func stopSendingQueue() {
    queueTask?.cancel()
    queueTask = nil
    isSendingQueue = false
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
