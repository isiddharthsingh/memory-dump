import SwiftUI
import UIKit

/// "Moodboard" tokens: white cards on a warm grey ground, each source and
/// topic with its own pastel, and one ink-blue accent for links, citations and
/// AI output. Status colors are kept for item state.
///
/// Each color has a dark value too. In dark the ground is a warm near-black,
/// cards sit a step lighter, ink turns off-white, and the accent turns
/// periwinkle so it still reads on black.
enum Palette {
  static let bg = Color(light: 0xFFFFFF, dark: 0x121211)
  static let ground = Color(light: 0xF0EEE9, dark: 0x121211)
  static let card = Color(light: 0xFFFFFF, dark: 0x1E1E1C)
  /// Bars and fields that float over the page, a step above cards in dark.
  static let raised = Color(light: 0xFFFFFF, dark: 0x242422)
  /// The edge of a raised bar, which a shadow alone can't show on black.
  static let raisedEdge = Color(dynamic: { $0.isDark ? .white.withAlphaComponent(0.07) : .clear })
  static let fill = Color(light: 0xEDEAE3, dark: 0x2A2A27)
  static let surfaceMuted = Color(light: 0xF7F6F2, dark: 0x191918)
  static let hairline = Color(light: 0xE8E6E0, dark: 0x2E2E2B)
  static let hairlineStrong = Color(light: 0xD9D6CE, dark: 0x3C3B37)

  static let ink = Color(light: 0x17171B, dark: 0xF2F0EB)
  /// Text and icons on an ink, accent or failed fill.
  static let onInk = Color(light: 0xFFFFFF, dark: 0x121211)
  /// Text on a topic pastel, which stays light in both modes.
  static let onPastel = Color(hex: 0x17171B)
  static let ink2 = Color(light: 0x5B5B63, dark: 0xB3B1AA)
  static let ink3 = Color(light: 0x6A6A72, dark: 0xA3A19A)
  static let inkReading = Color(light: 0x1F1F23, dark: 0xE6E4DE)

  /// Post cards: ink in light, black in dark, where `postEdge` outlines them.
  static let post = Color(light: 0x17171B, dark: 0x000000)
  static let postEdge = Color(dynamic: { $0.isDark ? .white.withAlphaComponent(0.1) : .clear })

  static let accent = Color(light: 0x2B4ACB, dark: 0x8FA3FF)
  static let accentSoft = Color(light: 0xEEF1FC, dark: 0x252B47)

  static let video = Color(light: 0xFF6B4A, dark: 0xE8603F)
  static let sand = Color(light: 0xEAD7AE, dark: 0xC9B78F)
  static let fresh = Color(light: 0x1F9D6B, dark: 0x3CC48C)

  static let processing = Color(light: 0x9A5B00, dark: 0xF2B24C)
  static let processingDot = Color(light: 0xD98A0B, dark: 0xF2B24C)
  static let processingSoft = Color(light: 0xFFF5E5, dark: 0x3A2A10)
  static let failed = Color(light: 0xC42B2B, dark: 0xFF8A80)
  static let failedSoft = Color(light: 0xFDEDED, dark: 0x3D1A18)
  static let ready = Color(light: 0x1F7A47, dark: 0x4CC27E)

  private static var shadows: [Double: Color] = [:]

  /// A drop shadow: soft ink in light; in dark a grey shadow vanishes, so it
  /// is black and stronger. Kept per opacity, so a view gets the same color
  /// on every render.
  static func shadow(_ opacity: Double) -> Color {
    if let shadow = shadows[opacity] { return shadow }
    let shadow = Color(dynamic: {
      $0.isDark ? .black.withAlphaComponent(min(1, opacity * 3.5)) : UIColor(hex: 0x17171B).withAlphaComponent(opacity)
    })
    shadows[opacity] = shadow
    return shadow
  }

  /// What covers the page behind a drawer.
  static let scrim = Color(dynamic: { .black.withAlphaComponent($0.isDark ? 0.55 : 0.18) })

  /// The dot that marks a source in filters and cards.
  static func sourceDot(_ source: String) -> Color {
    switch source {
    case "youtube": video
    case "twitter", "reddit": ink
    case "instagram": Color(hex: 0x9B87F5)
    case "pdf", "image": Color(hex: 0x5B9BE6)
    default: Color(hex: 0xE2B84A)
    }
  }
}

/// A pastel fill and the darker ink that reads on it. In dark the pastels dim
/// about 12% so they don't glare, and their inks deepen to match; titles on
/// them use `Palette.onPastel`.
struct Swatch: Hashable {
  let fill: Color
  let ink: Color

  static let butter = Swatch(fill: Color(light: 0xFFE27A, dark: 0xE3C65E), ink: Color(light: 0x5C4D0A, dark: 0x3D3205))
  static let salmon = Swatch(fill: Color(light: 0xFFC2B3, dark: 0xE8A797), ink: Color(light: 0x6B2A1C, dark: 0x4F1C11))
  static let mint = Swatch(fill: Color(light: 0xB5EBD3, dark: 0x93D1B6), ink: Color(light: 0x1F5641, dark: 0x133D2D))
  static let sky = Swatch(fill: Color(light: 0xBFDDFF, dark: 0x9EC3EE), ink: Color(light: 0x1D3F66, dark: 0x11304F))
  static let lilac = Swatch(fill: Color(light: 0xCDBDFF, dark: 0xAE9DEB), ink: Color(light: 0x3A3352, dark: 0x2A2342))
  static let pink = Swatch(fill: Color(light: 0xFFC6E0, dark: 0xE6A6C3), ink: Color(light: 0x6B2347, dark: 0x4D1731))

  private static let topics: [Swatch] = [.butter, .salmon, .mint, .sky, .lilac, .pink]

  /// The same topic gets the same color on every screen: FNV-1a of the slug,
  /// then murmur3's finalizer, without which most slugs landed on one color.
  static func topic(_ slug: String) -> Swatch {
    var h = Format.fnv1a(slug)
    h ^= h >> 16
    h = h &* 0x85EB_CA6B
    h ^= h >> 13
    h = h &* 0xC2B2_AE35
    h ^= h >> 16
    return topics[Int(h % UInt32(topics.count))]
  }
}

extension Color {
  nonisolated init(hex: UInt32) {
    self.init(uiColor: UIColor(hex: hex))
  }

  /// A color that follows light and dark mode, in SwiftUI and in UIKit through `UIColor(_:)`.
  nonisolated init(light: UInt32, dark: UInt32) {
    let light = UIColor(hex: light)
    let dark = UIColor(hex: dark)
    self.init(dynamic: { $0.isDark ? dark : light })
  }

  /// UIKit can resolve a dynamic color on any thread, so the provider is
  /// Sendable and never tied to the main actor.
  nonisolated init(dynamic provider: @escaping @Sendable (UITraitCollection) -> UIColor) {
    self.init(uiColor: UIColor(dynamicProvider: provider))
  }
}

extension UIColor {
  nonisolated convenience init(hex: UInt32) {
    self.init(
      red: CGFloat((hex >> 16) & 0xFF) / 255,
      green: CGFloat((hex >> 8) & 0xFF) / 255,
      blue: CGFloat(hex & 0xFF) / 255,
      alpha: 1)
  }
}

extension UITraitCollection {
  nonisolated var isDark: Bool { userInterfaceStyle == .dark }
}

/// The app's own light or dark setting, or the iPhone's. Kept in
/// UserDefaults under `key` and set on each scene, so every window in it,
/// with its sheets and alerts, follows it, including windows made later.
enum Appearance: String, CaseIterable, Identifiable {
  case system, light, dark

  static let key = "appearance"

  var id: Self { self }

  var label: String {
    switch self {
    case .system: "System"
    case .light: "Light"
    case .dark: "Dark"
    }
  }

  var style: UIUserInterfaceStyle {
    switch self {
    case .system: .unspecified
    case .light: .light
    case .dark: .dark
    }
  }

  static func apply(_ appearance: Appearance, animated: Bool) {
    for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
      let set = {
        if appearance == .system {
          scene.traitOverrides.remove(UITraitUserInterfaceStyle.self)
        } else {
          scene.traitOverrides.userInterfaceStyle = appearance.style
        }
      }
      if animated, let window = scene.keyWindow {
        UIView.transition(with: window, duration: 0.3, options: .transitionCrossDissolve, animations: set)
      } else {
        set()
      }
    }
  }
}

/// Bricolage Grotesque is for headings and cards, Newsreader for things you
/// read, and the system face for things you do.
extension Font {
  enum SerifWeight: String {
    case regular = "Newsreader-Regular"
    case medium = "Newsreader-Medium"
    case semibold = "Newsreader-SemiBold"
  }

  enum DisplayWeight: String {
    case semibold = "BricolageGrotesque-SemiBold"
    case bold = "BricolageGrotesque-Bold"
    case heavy = "BricolageGrotesque-ExtraBold"
  }

  static func serif(_ size: CGFloat, _ weight: SerifWeight = .regular, relativeTo style: TextStyle = .body)
    -> Font
  {
    .custom(weight.rawValue, size: size, relativeTo: style)
  }

  static func display(_ size: CGFloat, _ weight: DisplayWeight = .bold, relativeTo style: TextStyle = .title3)
    -> Font
  {
    .custom(weight.rawValue, size: size, relativeTo: style)
  }

  static let readingTitle = display(32, .heavy, relativeTo: .largeTitle)
  static let readingBody = serif(18)
  static let cardTitle = display(16, .bold, relativeTo: .headline)
}

enum Theme {
  /// Navigation titles in the display face. Set once at launch, before any bar exists.
  static func styleNavigationBars() {
    let bar = UINavigationBar.appearance()
    if let large = UIFont(name: Font.DisplayWeight.heavy.rawValue, size: 36) {
      bar.largeTitleTextAttributes = [
        .font: UIFontMetrics(forTextStyle: .largeTitle).scaledFont(for: large),
        .foregroundColor: UIColor(Palette.ink),
      ]
    }
    if let inline = UIFont(name: Font.DisplayWeight.bold.rawValue, size: 18) {
      bar.titleTextAttributes = [
        .font: UIFontMetrics(forTextStyle: .headline).scaledFont(for: inline),
        .foregroundColor: UIColor(Palette.ink),
      ]
    }
  }
}

enum Haptics {
  static func tap() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
  static func press() { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }
  static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
  static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
  static func selection() { UISelectionFeedbackGenerator().selectionChanged() }
}
