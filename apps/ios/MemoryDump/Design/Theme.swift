import SwiftUI
import UIKit

/// "Quiet Library" tokens, ported from the Expo app's lib/theme.ts. The UI stays
/// neutral so saved content carries the color; one ink-blue accent marks links,
/// citations and AI output, and status colors are kept for item state.
enum Palette {
  static let bg = Color(hex: 0xFFFFFF)
  static let fill = Color(hex: 0xF2F2F4)
  static let surfaceMuted = Color(hex: 0xF7F7F8)
  static let hairline = Color(hex: 0xEDEDEF)
  static let hairlineStrong = Color(hex: 0xDCDCE0)

  static let ink = Color(hex: 0x0B0B0C)
  static let ink2 = Color(hex: 0x5C5C61)
  static let ink3 = Color(hex: 0x6E6E73)
  static let inkReading = Color(hex: 0x1C1C1E)

  static let accent = Color(hex: 0x2B4ACB)
  static let accentSoft = Color(hex: 0xEEF1FC)

  static let processing = Color(hex: 0x9A5B00)
  static let processingDot = Color(hex: 0xD98A0B)
  static let processingSoft = Color(hex: 0xFFF5E5)
  static let failed = Color(hex: 0xC42B2B)
  static let failedSoft = Color(hex: 0xFDEDED)
  static let ready = Color(hex: 0x1F7A47)
}

extension Color {
  init(hex: UInt32) {
    self.init(
      red: Double((hex >> 16) & 0xFF) / 255,
      green: Double((hex >> 8) & 0xFF) / 255,
      blue: Double(hex & 0xFF) / 255)
  }
}

/// Newsreader is for things you read; the system face is for things you do.
extension Font {
  enum SerifWeight: String {
    case regular = "Newsreader-Regular"
    case medium = "Newsreader-Medium"
    case semibold = "Newsreader-SemiBold"
  }

  static func serif(_ size: CGFloat, _ weight: SerifWeight = .regular, relativeTo style: TextStyle = .body)
    -> Font
  {
    .custom(weight.rawValue, size: size, relativeTo: style)
  }

  static let readingTitle = serif(30, .semibold, relativeTo: .largeTitle)
  static let readingBody = serif(18)
  static let cardTitle = serif(19, .medium, relativeTo: .title3)
}

enum Haptics {
  static func tap() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
  static func press() { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }
  static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
  static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
  static func selection() { UISelectionFeedbackGenerator().selectionChanged() }
}
