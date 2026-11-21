import Foundation
import Testing

@testable import MemoryDump

@Suite struct DialCodeTests {
  @Test func everyCountryHasACodeOfOneToThreeDigits() {
    #expect(DialCode.all.count > 230)
    #expect(Set(DialCode.all.map(\.region)).count == DialCode.all.count)
    for country in DialCode.all {
      #expect(country.region.count == 2)
      #expect(
        (1...3).contains(country.code.count) && country.code.allSatisfy { $0.isASCII && $0.isNumber },
        "\(country.region)")
      #expect(!country.code.hasPrefix("0"), "\(country.region)")
    }
  }

  @Test func knowsTheUsualOnes() {
    func code(_ region: String) -> String? { DialCode.all.first { $0.region == region }?.code }
    #expect(code("US") == "1")
    #expect(code("IN") == "91")
    #expect(code("GB") == "44")
    #expect(code("AE") == "971")
    #expect(code("AC") == "247")
    #expect(code("XK") == "383")
    #expect(DialCode(region: "IN", code: "91").flag == "🇮🇳")
  }

  @Test func putsTheCodeInFrontOfTheTypedDigits() {
    let india = DialCode(region: "IN", code: "91")
    #expect(india.number("98765 43210") == "+919876543210")
    #expect(DialCode(region: "US", code: "1").number("(415) 555-0132") == "+14155550132")
    // Digits typed on another script's keypad.
    #expect(india.number("٩٨٧٦٥٤٣٢١٠") == "+919876543210")
  }

  /// What people type in front of a number at home isn't part of it abroad.
  @Test func leavesOutNationalAndInternationalPrefixes() {
    let india = DialCode(region: "IN", code: "91")
    let britain = DialCode(region: "GB", code: "44")
    #expect(britain.number("07911 123456") == "+447911123456")
    #expect(india.number("098765 43210") == "+919876543210")
    // The country code typed again after picking it, also where numbers come
    // in many lengths and the whole would pass for one.
    #expect(india.number("91 98765 43210") == "+919876543210")
    #expect(DialCode(region: "DE", code: "49").number("49 1512 3456789") == "+4915123456789")
    #expect(DialCode(region: "SE", code: "46").number("46 8 12 34 56") == "+468123456")
    // A number that only starts like its country's code stays whole.
    #expect(DialCode(region: "SG", code: "65").number("6512 3456") == "+6565123456")
    #expect(DialCode(region: "NO", code: "47").number("47 12 34 56") == "+4747123456")
    #expect(india.number("0044 7911 123456") == "+447911123456")
    // Italy's leading 0 is part of the number.
    #expect(DialCode(region: "IT", code: "39").number("02 1234 5678") == "+390212345678")
  }

  /// A saved number goes back into the phone field as its country and the
  /// number as written there, and reads as the same number again.
  @Test func splitsASavedNumberIntoItsCountryAndNationalNumber() throws {
    for saved in ["+919876543210", "+447400123456", "+14155550132", "+390212345678"] {
      let split = try #require(DialCode.split(saved))
      #expect(!split.national.hasPrefix("+"))
      #expect(split.country.number(split.national) == saved)
    }
    #expect(DialCode.split("+919876543210")?.country.region == "IN")
    #expect(DialCode.split("+447400123456")?.country.region == "GB")
    // Guernsey shares +44; its own numbers get its own flag.
    #expect(DialCode.split("+447911123456")?.country.region == "GG")
    #expect(DialCode.split("+9112345") == nil)
    #expect(DialCode.split("") == nil)
  }

  @Test func refusesWhatIsNotANumberOfTheCountry() {
    #expect(DialCode(region: "IN", code: "91").number("12345") == nil)
    #expect(DialCode(region: "US", code: "1").number("555 0132") == nil)
    #expect(DialCode(region: "GB", code: "44").number("") == nil)
    #expect(DialCode(region: "GB", code: "44").number("+1 23") == nil)
  }

  /// AutoFill and paste give whole numbers; the picked country doesn't apply.
  @Test func keepsANumberThatAlreadyHasItsCountryCode() {
    let india = DialCode(region: "IN", code: "91")
    #expect(india.number(" +1 (415) 555-0132") == "+14155550132")
    // As copied from Contacts, between invisible direction marks.
    #expect(india.number("\u{202D}+1 (415) 555-0132\u{202C}") == "+14155550132")
    #expect(india.number("＋1 415 555 0132") == "+14155550132")
    #expect(!DialCode.hasOwnCode("415 555 0132"))
    #expect(!DialCode.hasOwnCode(""))
  }

  @Test func findsCountriesByNameOrCode() {
    #expect(DialCode.matching("").count == DialCode.all.count)
    #expect(DialCode.matching("+91").map(\.region) == ["IN"])
    #expect(DialCode.matching("44").map(\.region).contains("GB"))
    #expect(DialCode.matching(DialCode(region: "JP", code: "81").name).map(\.region) == ["JP"])
    #expect(DialCode.matching("٩١").map(\.region) == ["IN"])
    // A name is found without its accents (Åland Islands, in English).
    let plain = DialCode(region: "AX", code: "358").name.folding(options: .diacriticInsensitive, locale: nil)
    #expect(DialCode.matching(plain).map(\.region).contains("AX"))
  }
}
