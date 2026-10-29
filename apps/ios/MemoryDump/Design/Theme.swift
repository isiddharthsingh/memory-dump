import SwiftUI
import UIKit

/// "Moodboard" tokens: white cards on a warm grey ground, each source and
/// topic with its own pastel, and one ink-blue accent for links, citations and
/// AI output. Status colors are kept for item state.
enum Palette {
  static let bg = Color(hex: 0xFFFFFF)
  static let ground = Color(hex: 0xF0EEE9)
  static let card = Color(hex: 0xFFFFFF)
  static let fill = Color(hex: 0xEDEAE3)
  static let surfaceMuted = Color(hex: 0xF7F6F2)
  static let hairline = Color(hex: 0xE8E6E0)
  static let hairlineStrong = Color(hex: 0xD9D6CE)

  static let ink = Color(hex: 0x17171B)
  static let ink2 = Color(hex: 0x5B5B63)
  static let ink3 = Color(hex: 0x6A6A72)
  static let inkReading = Color(hex: 0x1F1F23)

  static let accent = Color(hex: 0x2B4ACB)
  static let accentSoft = Color(hex: 0xEEF1FC)

  static let video = Color(hex: 0xFF6B4A)
  static let sand = Color(hex: 0xEAD7AE)
  static let fresh = Color(hex: 0x1F9D6B)

  static let processing = Color(hex: 0x9A5B00)
  static let processingDot = Color(hex: 0xD98A0B)
  static let processingSoft = Color(hex: 0xFFF5E5)
  static let failed = Color(hex: 0xC42B2B)
  static let failedSoft = Color(hex: 0xFDEDED)
  static let ready = Color(hex: 0x1F7A47)

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

/// A pastel fill and the darker ink that reads on it.
struct Swatch: Hashable {
  let fill: Color
  let ink: Color

  static let butter = Swatch(fill: Color(hex: 0xFFE27A), ink: Color(hex: 0x5C4D0A))
  static let salmon = Swatch(fill: Color(hex: 0xFFC2B3), ink: Color(hex: 0x6B2A1C))
  static let mint = Swatch(fill: Color(hex: 0xB5EBD3), ink: Color(hex: 0x1F5641))
  static let sky = Swatch(fill: Color(hex: 0xBFDDFF), ink: Color(hex: 0x1D3F66))
  static let lilac = Swatch(fill: Color(hex: 0xCDBDFF), ink: Color(hex: 0x3A3352))
  static let pink = Swatch(fill: Color(hex: 0xFFC6E0), ink: Color(hex: 0x6B2347))

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
  init(hex: UInt32) {
    self.init(
      red: Double((hex >> 16) & 0xFF) / 255,
      green: Double((hex >> 8) & 0xFF) / 255,
      blue: Double(hex & 0xFF) / 255)
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

  static let readingTitle = display(30, .heavy, relativeTo: .largeTitle)
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
