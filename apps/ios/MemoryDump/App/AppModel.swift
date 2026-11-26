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
  /// The server's 426 detail: this build is too old for it. Shown as an alert.
  var updateNotice: String?
  let router = Router()
  /// Bumped each time the app becomes active, so the Library reloads and shows
  /// what the share extension saved while the app was in the background.
  private(set) var libraryVersion = 0
  /// Shares the server hasn't taken yet, oldest first.
  private(set) var queued: [QueuedShare] = []
  private(set) var isSendingQueue = false

  private let store = SessionStore.shared
  /// Counts the profile saves the server took, so `refreshProfile` can tell
  /// that its answer was asked for before one.
  private var profileSaves = 0
  private var toastTask: Task<Void, Never>?
  private var queueTask: Task<Void, Never>?
  private var isActive = false
  private let network = NWPathMonitor()

  init() {
    session = store.current()
    store.onSignedOut = { [weak self] in
      Self.forgetSavedCopies(queue: false)
      self?.session = nil
      self?.stopSendingQueue()
    }
    APIClient.shared.onUpdateRequired = { [weak self] message in
      Haptics.error()
      self?.updateNotice = message
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

  /// A nil phone signs up without one.
  func signUp(email: String, password: String, name: String, phone: String?) async throws {
    finishSignIn(try await AuthAPI.signUp(email: email, password: password, name: name, phone: phone))
  }

  func signOut() async {
    // Best effort: the local session is cleared either way.
    if session != nil {
      // While the session still works: this phone stops getting the
      // account's pushes.
      if let token = UserDefaults.standard.string(forKey: Self.pushTokenKey) {
        try? await APIClient.shared.unregisterDevice(token: token)
      }
      try? await APIClient.shared.signOut()
    }
    stopSendingQueue()
    store.clear()
    Self.forgetSavedCopies(queue: true)
    queued = []
    session = nil
  }

  func deleteAccount() async throws {
    try await APIClient.shared.deleteAccount()
    stopSendingQueue()
    store.clear()
    Self.forgetSavedCopies(queue: true)
    queued = []
    session = nil
  }

  /// The session keeps the account as it was at sign-in; this brings its name,
  /// phone and AI consent up to date with the server.
  func refreshProfile() async {
    let saves = profileSaves
    // Not over a save made meanwhile: its answer is the newer one.
    guard let user = try? await APIClient.shared.profile(), saves == profileSaves else { return }
    await keep(user)
  }

  /// Nil leaves the name or the phone as it is; an empty phone removes it.
  func updateProfile(name: String?, phone: String?) async throws {
    let user = try await APIClient.shared.updateProfile(name: name, phone: phone)
    profileSaves += 1
    await keep(user)
  }

  /// Agrees to AI processing, or withdraws it. Without it RootView shows
  /// ConsentView instead of the app, and the server does no AI work.
  func setAIConsent(_ consent: Bool) async throws {
    let user = try await APIClient.shared.setAIConsent(consent)
    profileSaves += 1
    await keep(user)
  }

  private func keep(_ user: User) async {
    let waited = session?.user.aiConsentAt == nil
    if let updated = await store.setUser(user) { session = updated }
    // The queue waits for consent (sendQueue); send it once consent arrives,
    // given here or on another device.
    if waited, session?.user.aiConsentAt != nil { sendQueue() }
  }

  private func finishSignIn(_ session: Session) {
    Haptics.success()
    store.save(session)
    self.session = session
    // Shares another account queued before its session ended aren't this one's.
    ShareQueue.keepOnly(session.user.id)
    sendQueue()
    Task { await registerForPush() }
    Task { await OfflineSync.run() }
  }

  /// Nothing from an account stays on the phone after it signs out. Its
  /// unsent shares go too when the person signs out or deletes the account
  /// (`queue`); when the session just ended (refused, or the account deleted
  /// elsewhere) they wait for the next sign-in, as the share extension says.
  private static func forgetSavedCopies(queue: Bool) {
    SavedResponses.shared.clear()
    ImageCache.clear()
    APIClient.forgetExports()
    PlaceLocator.forget()
    ShareQueue.clearInbox()
    if queue { ShareQueue.clear() }
  }

  // MARK: Becoming active

  func becameActive() async {
    isActive = true
    // Asks once more whether the server still refuses this build.
    APIClient.shared.checkBuildAgain()
    // The extension can rotate the tokens while the app is in the background.
    if session != nil {
      session = store.current()
      // Or ended it, when the server refused its refresh there.
      if session == nil { Self.forgetSavedCopies(queue: false) }
    }
    guard session != nil else { return }
    // Consent given or withdrawn on another device, and a session saved
    // before the app knew of consent, come up to date.
    Task { await refreshProfile() }
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
  /// that, or once the server's Retry-After has passed if that's later, for as
  /// long as anything is left, someone is signed in and has agreed to AI
  /// processing, the server takes this build and the app is in front. Without
  /// that consent every save would fail on the server, and a file's would be
  /// deleted there, so the queue waits for it (`keep` sends it then). Called
  /// again (a new network, Retry) while it waits, it tries at once unless
  /// Retry-After hasn't passed; a send under way carries on.
  func sendQueue() {
    queued = ShareQueue.pending()
    guard isActive, session?.user.aiConsentAt != nil, !queued.isEmpty, !isSendingQueue else { return }
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
        if queued.isEmpty || session?.user.aiConsentAt == nil || !isActive
          || APIClient.shared.updateRequired != nil
        {
          return
        }
        let retryAfter = ShareQueue.notBefore?.timeIntervalSinceNow ?? 0
        try? await Task.sleep(for: .seconds(max(Double(pause), retryAfter)))
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

  /// Where the last push token APNs gave this phone is kept, so signing out
  /// can take it off the account.
  private static let pushTokenKey = "push-token"

  /// Asks for permission, then registers. The token arrives in AppDelegate.
  func registerForPush() async {
    let center = UNUserNotificationCenter.current()
    let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
    if granted { UIApplication.shared.registerForRemoteNotifications() }
  }

  func didReceivePushToken(_ token: Data) {
    let hex = token.map { String(format: "%02x", $0) }.joined()
    UserDefaults.standard.set(hex, forKey: Self.pushTokenKey)
    guard session != nil else { return }
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
