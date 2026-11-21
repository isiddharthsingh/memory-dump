import Foundation
import PhoneNumberKit

/// A country and the code its phone numbers start with.
struct DialCode: Identifiable, Hashable {
  /// ISO 3166-1 alpha-2.
  let region: String
  /// The calling code's digits, without the `+`.
  let code: String
  /// The country's name in the phone's language.
  let name: String

  init(region: String, code: String) {
    self.region = region
    self.code = code
    name = Locale.current.localizedString(forRegionCode: region) ?? region
  }

  var id: String { region }
  /// The region's two letters as regional indicator symbols.
  var flag: String {
    String(String.UnicodeScalarView(region.unicodeScalars.compactMap { Unicode.Scalar(0x1F1A5 + $0.value) }))
  }

  /// The number to send, `+`, the country code and the national number as
  /// digits (E.164), or nil when what was typed isn't a phone number of this
  /// country, or of the one its own `+` names. Read by libphonenumber's rules,
  /// so a national prefix (the 0 of 07911…), an international one (0044…) or
  /// this code typed again is left out, where pasting the code in front would
  /// keep them.
  func number(_ typed: String) -> String? {
    let digits = Self.digits(typed)
    if Self.hasOwnCode(typed) { return Self.e164("+" + digits, region: region) }
    // libphonenumber keeps a repeated code whenever the whole is a possible
    // number too, as it is where numbers come in many lengths (Germany,
    // Sweden, Indonesia). The code was just picked, so read its repeat as one.
    if digits.hasPrefix(code), let number = Self.e164(String(digits.dropFirst(code.count)), region: region) {
      return number
    }
    return Self.e164(digits, region: region)
  }

  private static func e164(_ number: String, region: String) -> String? {
    guard let parsed = try? numbers.parse(number, withRegion: region, ignoreType: true) else { return nil }
    return numbers.format(parsed, toType: .e164)
  }

  /// A saved number as the phone field shows it: the country it belongs to
  /// and the number as written there. Nil when it isn't one libphonenumber reads.
  static func split(_ number: String) -> (country: DialCode, national: String)? {
    guard let parsed = try? numbers.parse(number, withRegion: local.region, ignoreType: true) else { return nil }
    let region = numbers.getRegionCode(of: parsed) ?? numbers.mainCountry(forCode: parsed.countryCode)
    guard let country = all.first(where: { $0.region == region }) else { return nil }
    return (country, numbers.format(parsed, toType: .national))
  }

  /// What to say when `number` reads nothing from what was typed.
  static let refusal = "That doesn’t look like a phone number. Check the country code and the digits."

  /// libphonenumber's rules and every region's code. It reads its metadata
  /// when made, so there is one.
  private static let numbers = PhoneNumberUtility()

  /// The digits in `text` as 0-9, whichever script's keypad typed them.
  static func digits(_ text: String) -> String {
    text.compactMap(\.wholeNumberValue).filter { $0 < 10 }.map(String.init).joined()
  }

  /// Whether a typed number starts with a `+`, as one from AutoFill or a paste
  /// does. A number copied from Contacts has invisible marks around it, so
  /// this looks at the first plus or digit, not the first character.
  static func hasOwnCode(_ typed: String) -> Bool {
    let plus: Set<Character> = ["+", "＋"]
    guard let first = typed.first(where: { plus.contains($0) || $0.wholeNumberValue != nil }) else { return false }
    return plus.contains(first)
  }

  /// Every country libphonenumber knows, by name in the phone's language. Its
  /// "001" entries are codes no country owns (satellite and toll-free ones).
  /// The North American plan's members all share 1: their area codes are part
  /// of the number the person types.
  static let all: [DialCode] = numbers.allCountries()
    .filter { $0.count == 2 }
    .compactMap { region in numbers.countryCode(for: region).map { DialCode(region: region, code: String($0)) } }
    .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }

  /// The country the phone is set to, or the United States.
  static var local: DialCode {
    let region = Locale.current.region?.identifier
    return all.first { $0.region == region } ?? DialCode(region: "US", code: "1")
  }

  /// The countries whose name contains `query`, whatever its case or accents,
  /// or whose code starts with its digits.
  static func matching(_ query: String) -> [DialCode] {
    let query = query.trimmingCharacters(in: .whitespaces)
    guard !query.isEmpty else { return all }
    let code = digits(query)
    return all.filter { $0.name.localizedStandardContains(query) || (!code.isEmpty && $0.code.hasPrefix(code)) }
  }
}
