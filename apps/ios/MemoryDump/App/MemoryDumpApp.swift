import SwiftUI
import UserNotifications

@main
struct MemoryDumpApp: App {
  @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
  @Environment(\.scenePhase) private var scenePhase

  var body: some Scene {
    WindowGroup {
      RootView()
        .environment(delegate.app)
        .environment(delegate.app.router)
    }
    .onChange(of: scenePhase) { _, phase in
      if phase == .active { Task { await delegate.app.becameActive() } }
    }
  }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
  let app = AppModel()

  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    UNUserNotificationCenter.current().delegate = self
    Theme.styleNavigationBars()
    if app.session != nil { Task { await app.registerForPush() } }
    return true
  }

  func application(
    _ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
  ) {
    app.didReceivePushToken(deviceToken)
  }

  // Show pushes while the app is open, as the Expo app did.
  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter, willPresent notification: UNNotification
  ) async -> UNNotificationPresentationOptions {
    [.banner, .list, .sound]
  }
}

struct RootView: View {
  @Environment(AppModel.self) private var app

  var body: some View {
    Group {
      if app.session == nil {
        SignInView()
      } else {
        MainTabs()
      }
    }
    .overlay(alignment: .bottom) {
      if let toast = app.toast {
        ToastView(toast: toast) { app.toast = nil }
          .padding(.horizontal, 20)
          .padding(.bottom, 90)
          .transition(.move(edge: .bottom).combined(with: .opacity))
      }
    }
    .animation(.snappy, value: app.toast)
    .tint(Palette.accent)
  }
}

private struct MainTabs: View {
  @Environment(Router.self) private var router

  var body: some View {
    @Bindable var router = router
    TabView(selection: $router.tab) {
      Tab("Library", systemImage: "books.vertical", value: AppTab.library) {
        NavigationStack(path: $router.libraryPath) {
          LibraryView().withRoutes()
        }
      }
      Tab("Wiki", systemImage: "book.closed", value: AppTab.wiki) {
        NavigationStack(path: $router.wikiPath) {
          WikiListView().withRoutes()
        }
      }
      Tab("Ask", systemImage: "sparkles", value: AppTab.ask) {
        NavigationStack(path: $router.askPath) {
          AskView().withRoutes()
        }
      }
    }
    .tint(Palette.ink)
    .onChange(of: router.tab) { Haptics.selection() }
    // Above the stacks, so pushed screens get it too.
    .environment(\.openURL, OpenURLAction { router.open($0) })
  }
}

private struct RouteLinks: ViewModifier {
  func body(content: Content) -> some View {
    content
      .navigationDestination(for: Route.self) { route in
        switch route {
        case .item(let id): ItemReaderView(id: id)
        case .wiki(let slug): WikiPageView(slug: slug)
        }
      }
  }
}

extension View {
  fileprivate func withRoutes() -> some View { modifier(RouteLinks()) }
}

private struct ToastView: View {
  let toast: AppModel.Toast
  let dismiss: () -> Void

  var body: some View {
    Button(action: dismiss) {
      HStack(spacing: 12) {
        Image(systemName: toast.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
        Text(toast.message).font(.callout.weight(.medium)).multilineTextAlignment(.leading)
        Spacer(minLength: 0)
      }
      .foregroundStyle(.white)
      .padding(.horizontal, 16)
      .padding(.vertical, 14)
      .background(toast.ok ? Palette.ink : Palette.failed, in: .rect(cornerRadius: 16))
      .shadow(color: .black.opacity(0.1), radius: 24, y: 8)
    }
    .buttonStyle(.plain)
    .accessibilityHint("Tap to dismiss.")
  }
}
