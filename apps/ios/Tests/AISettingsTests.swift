import Foundation
import Testing

@testable import MemoryDump

@Suite struct AISettingsTests {
  private static let json = #"""
    {"admin": false, "keys": true, "provider": "bedrock",
     "free": {"limit": 3.0, "spent": 0.4213, "resets": "2026-12-01"},
     "own": {"limit": 10.0, "spent": 1.2},
     "providers": [
       {"id": "anthropic", "name": "Anthropic", "placeholder": "sk-ant-…",
        "help": "Create a key at console.anthropic.com under API keys.", "region": false, "key": null,
        "models": [{"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5"},
                   {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5"}],
        "chosen": {"analysis": "claude-haiku-5-5", "synthesis": "claude-sonnet-5-5", "ask": "claude-opus-5-5"}},
       {"id": "bedrock", "name": "Amazon Bedrock", "placeholder": "ABSK…", "help": "Create a Bedrock API key.",
        "region": true, "key": {"hint": "x9Qz", "region": "us-east-1", "added": "2026-11-19T20:01:02.123456Z"},
        "models": [{"id": "nova-pro", "name": "Nova Pro"}],
        "chosen": {"analysis": "nova-pro", "synthesis": "nova-pro", "ask": "nova-pro"}}
     ]}
    """#

  private static func provider(_ id: String, _ name: String, key: Bool) -> AISettings.Provider {
    AISettings.Provider(
      id: id, name: name, placeholder: "sk-…", help: "Create a key.", region: false,
      key: key ? .init(hint: "x9Qz", region: nil, added: .now) : nil,
      models: [.init(id: "small", name: "Small"), .init(id: "large", name: "Large")],
      chosen: .init(analysis: "small", synthesis: "large", ask: "large"))
  }

  private static func voyage(key: Bool = false, limit: Double? = nil, spent: Double = 0) -> AISettings.Voyage {
    AISettings.Voyage(
      id: "voyage", name: "Voyage", placeholder: "pa-…", help: "Create a key at dashboard.voyageai.com under API keys.",
      key: key ? .init(hint: "VOYG", region: nil, added: .now) : nil, limit: limit, spent: spent)
  }

  private static func settings(
    admin: Bool = false, keys: Bool = true, provider: String? = nil, free: Double? = 3, spent: Double = 0.4213,
    ownLimit: Double? = nil, ownSpent: Double = 0, voyage: AISettings.Voyage? = nil
  ) -> AISettings {
    AISettings(
      admin: admin, keys: keys, provider: provider,
      free: .init(limit: free, spent: spent, resets: "2026-12-01"),
      own: .init(limit: ownLimit, spent: ownSpent), voyage: voyage,
      providers: [Self.provider("anthropic", "Anthropic", key: false), Self.provider("openai", "OpenAI", key: true)])
  }

  @Test func decodesProvidersWithAndWithoutAKey() throws {
    let ai = try JSONDecoder.api.decode(AISettings.self, from: Data(Self.json.utf8))
    #expect(!ai.admin)
    #expect(ai.keys)
    #expect(ai.provider == "bedrock")
    #expect(ai.free == .init(limit: 3, spent: 0.4213, resets: "2026-12-01"))
    #expect(ai.own == .init(limit: 10, spent: 1.2))
    #expect(ai.providers.map(\.id) == ["anthropic", "bedrock"])
    let anthropic = ai.providers[0]
    #expect(anthropic.key == nil)
    #expect(!anthropic.region)
    #expect(anthropic.placeholder == "sk-ant-…")
    #expect(anthropic.models.map(\.name) == ["Claude Haiku 5.5", "Claude Sonnet 5.5"])
    #expect(anthropic.chosen == .init(analysis: "claude-haiku-5-5", synthesis: "claude-sonnet-5-5", ask: "claude-opus-5-5"))
    let bedrock = ai.providers[1]
    #expect(bedrock.region)
    #expect(bedrock.key?.hint == "x9Qz")
    #expect(bedrock.key?.region == "us-east-1")
    #expect(abs((bedrock.key?.added.timeIntervalSince1970 ?? 0) - 1_795_118_462.123) < 0.001)
    #expect(ai.inUse?.name == "Amazon Bedrock")
  }

  /// An admin has no free limit, and nobody has a limit of their own until they set one.
  @Test func decodesAnAdminWithNoLimits() throws {
    let json = #"""
      {"admin": true, "keys": false, "provider": null,
       "free": {"limit": null, "spent": 4.2, "resets": "2026-12-01"},
       "own": {"limit": null, "spent": 0}, "providers": []}
      """#
    let ai = try JSONDecoder.api.decode(AISettings.self, from: Data(json.utf8))
    #expect(ai.admin)
    #expect(!ai.keys)
    #expect(ai.provider == nil)
    #expect(ai.free.limit == nil)
    #expect(ai.own.limit == nil)
    #expect(ai.inUse == nil)
  }

  /// "No provider" and "no limit" are nulls the server reads, not missing keys.
  @Test func aNilProviderOrLimitIsSentAsNull() throws {
    func text(_ body: some Encodable) throws -> String {
      String(decoding: try JSONEncoder().encode(body), as: UTF8.self)
    }
    #expect(try text(AISettings.ProviderBody(provider: nil)) == #"{"provider":null}"#)
    #expect(try text(AISettings.ProviderBody(provider: "openai")) == #"{"provider":"openai"}"#)
    #expect(try text(AISettings.LimitBody(limit: nil)) == #"{"limit":null}"#)
    #expect(try text(AISettings.LimitBody(limit: 10)) == #"{"limit":10}"#)
    #expect(try text(AISettings.LimitBody(limit: 12.5)) == #"{"limit":12.5}"#)
  }

  /// A key saved without a region says so with a null too, as APIClient.saveKey builds it.
  @Test func aKeyWithoutARegionSendsANullRegion() throws {
    let body: [String: String?] = ["key": "sk-test", "region": nil]
    let sent = try JSONSerialization.jsonObject(with: JSONEncoder().encode(body)) as? [String: Any]
    #expect(sent?["key"] as? String == "sk-test")
    #expect(sent?["region"] is NSNull)
  }

  @Test func theSettingsRowSaysWhoseModelsAreInUse() {
    #expect(Self.settings().summary == "$0.42 of $3 free")
    #expect(Self.settings(spent: 3.07).summary == "$3.00 of $3 free")
    #expect(Self.settings(admin: true, free: nil, spent: 4.2).summary == "No limit")
    #expect(Self.settings(provider: "openai", ownSpent: 1.2).summary == "Your OpenAI key")
    #expect(Self.settings(admin: true, provider: "openai", free: nil).summary == "Your OpenAI key")
  }

  @Test func theFreeAmountFillsABarUntilItIsUsedUp() {
    let some = Self.settings().usage
    #expect(some.text == "$0.42 of $3.00 free")
    #expect(abs((some.fraction ?? 0) - 0.4213 / 3) < 0.0001)
    #expect(!some.isUsedUp)
    #expect(some.footer == "Resets Dec 1. With your own key there’s no cap.")

    let none = Self.settings(spent: 3.0001).usage
    #expect(none.text == "You’ve used this month’s free $3.00. You can add your own API key, or wait until Dec 1.")
    #expect(none.fraction == 1)
    #expect(none.isUsedUp)
    #expect(none.footer.isEmpty)
  }

  @Test func anAdminSeesTheSpendWithNoBar() {
    let usage = Self.settings(admin: true, free: nil, spent: 4.2).usage
    #expect(usage == .init(text: "$4.20 spent this month", footer: "No limit on this account."))
  }

  @Test func anOwnKeyShowsItsSpendAgainstTheLimitWhenThereIsOne() {
    let open = Self.settings(provider: "openai", ownSpent: 1.2).usage
    #expect(open == .init(text: "$1.20 on your OpenAI key this month", footer: "Resets Dec 1."))

    let limited = Self.settings(provider: "openai", ownLimit: 10, ownSpent: 1.2).usage
    #expect(limited.text == "$1.20 on your OpenAI key this month")
    #expect(abs((limited.fraction ?? 0) - 0.12) < 0.0001)
    #expect(!limited.isUsedUp)
    #expect(limited.footer == "Of your $10.00 limit. Resets Dec 1.")

    let reached = Self.settings(provider: "openai", ownLimit: 10, ownSpent: 10.02).usage
    #expect(reached.fraction == 1)
    #expect(reached.isUsedUp)
    #expect(reached.footer == "You’ve reached your $10.00 limit. Memory Dump won’t use the key again until Dec 1.")
  }

  /// A copy saved before the server said anything of Voyage still opens.
  @Test func decodesVoyageWhenTheServerSendsIt() throws {
    let old = try JSONDecoder.api.decode(AISettings.self, from: Data(Self.json.utf8))
    #expect(old.voyage == nil)
    #expect(old.voyageUsage == nil)

    let json = Self.json.replacingOccurrences(
      of: #""providers": ["#,
      with: #"""
        "voyage": {"id": "voyage", "name": "Voyage", "placeholder": "pa-…", "help": "Create a key.",
                   "key": {"hint": "VOYG", "region": null, "added": "2026-11-19T20:01:02.123456Z"},
                   "limit": null, "spent": 0.0936},
        "providers": [
        """#)
    let ai = try JSONDecoder.api.decode(AISettings.self, from: Data(json.utf8))
    #expect(ai.voyage?.id == "voyage")
    #expect(ai.voyage?.key?.hint == "VOYG")
    #expect(ai.voyage?.limit == nil)
    #expect(ai.voyage?.spent == 0.0936)
    #expect(ai.usage.text == "$1.20 on your Amazon Bedrock and Voyage keys this month")
  }

  /// On their own models, Memory Dump's Voyage key is free up to its own
  /// amount; a Voyage key of their own takes its place.
  @Test func searchShowsTheFreeVoyageAmountUntilTheUserBringsAKey() {
    let help = "Create a key at dashboard.voyageai.com under API keys."
    let what = "Voyage indexes and searches what you save."

    let free = Self.settings(provider: "openai", voyage: Self.voyage(limit: 1, spent: 0.25)).voyageUsage
    #expect(free?.text == "$0.25 of $1.00 free on Memory Dump’s Voyage key")
    #expect(free?.fraction == 0.25)
    #expect(free?.isUsedUp == false)
    #expect(free?.footer == "\(what) Resets Dec 1. With your own key there’s no cap. \(help)")

    let used = Self.settings(provider: "openai", voyage: Self.voyage(limit: 1, spent: 1.004)).voyageUsage
    #expect(used?.text == "You’ve used this month’s free $1.00 of Voyage. You can add your own key, or wait until Dec 1.")
    #expect(used?.fraction == 1)
    #expect(used?.isUsedUp == true)

    let own = Self.settings(provider: "openai", voyage: Self.voyage(key: true, spent: 1.004)).voyageUsage
    #expect(own == .init(text: "", footer: "\(what) Your key is stored encrypted and never shown again."))

    // An admin on their own models has no cap to show.
    let admin = Self.settings(admin: true, provider: "openai", voyage: Self.voyage()).voyageUsage
    #expect(admin == .init(text: "", footer: "\(what) It runs on Memory Dump’s key. \(help)"))
  }

  /// On Memory Dump's models Voyage is part of the free amount, so nothing
  /// offers a key for it; one already saved still shows, to be removed.
  @Test func searchIsOnlyOfferedWhereAVoyageKeyChangesSomething() {
    #expect(Self.settings(voyage: Self.voyage()).voyageUsage == nil)
    #expect(Self.settings(voyage: Self.voyage(key: true)).voyageUsage != nil)
    #expect(Self.settings(keys: false, provider: "openai", voyage: Self.voyage(limit: 1)).voyageUsage == nil)
    #expect(Self.settings(provider: "openai").voyageUsage == nil)
  }

  /// With nowhere to keep keys, nothing offers one.
  @Test func aServerThatCannotStoreKeysSaysSoOnce() {
    let usage = Self.settings(keys: false).usage
    #expect(usage.footer == "Resets Dec 1. Using your own API keys isn’t available yet.")
    let usedUp = Self.settings(keys: false, spent: 3).usage
    #expect(usedUp.text == "You’ve used this month’s free $3.00. It’s free again on Dec 1.")
    #expect(usedUp.footer == "Using your own API keys isn’t available yet.")
  }

  @Test func aLimitIsAnAmountOverZero() {
    #expect(AISettings.amount("10") == 10)
    #expect(AISettings.amount(" 12.50 ") == 12.5)
    #expect(AISettings.amount("7,5") == 7.5)
    #expect(AISettings.amount("0") == nil)
    #expect(AISettings.amount("-3") == nil)
    #expect(AISettings.amount("") == nil)
    #expect(AISettings.amount("ten") == nil)
  }

  /// A model the provider no longer lists is still what the menu shows.
  @Test func aJobsMenuAlwaysHoldsTheModelInUse() {
    let provider = Self.provider("openai", "OpenAI", key: true)
    #expect(provider.options(including: "large").map(\.id) == ["small", "large"])
    #expect(provider.options(including: "retired").map(\.id) == ["small", "large", "retired"])
  }

  @Test func eachJobReadsAndSetsItsOwnModel() {
    var chosen = AISettings.Provider.Chosen(analysis: "a", synthesis: "s", ask: "q")
    #expect(AISettings.Job.allCases.map { chosen[$0] } == ["a", "s", "q"])
    #expect(AISettings.Job.allCases.map(\.title) == ["Reading saves", "Writing the wiki", "Answering"])
    chosen[.synthesis] = "large"
    #expect(chosen == .init(analysis: "a", synthesis: "large", ask: "q"))
  }
}

@Suite struct MoneyTests {
  @Test func dollarsAndCents() {
    #expect(Format.money(0.4213) == "$0.42")
    #expect(Format.money(12.5) == "$12.50")
    #expect(Format.money(1234.567) == "$1234.57")
    #expect(Format.money(0) == "$0.00")
    #expect(Format.money(0.004) == "<$0.01")
    #expect(Format.money(0.006) == "$0.01")
  }

  @Test func aWholeAmountCanDropItsCents() {
    #expect(Format.money(3, short: true) == "$3")
    #expect(Format.money(12.5, short: true) == "$12.50")
    #expect(Format.money(0, short: true) == "$0")
  }

  @Test func aDayWithoutATime() {
    #expect(Format.day("2026-12-01") == "Dec 1")
    #expect(Format.day("2027-01-01") == "Jan 1")
    #expect(Format.day("soon") == "soon")
  }

  /// Ask shows the server's sentence for a used-up month, not "something went wrong".
  @Test func askSaysWhichLimitWasReached() {
    let reached = Format.friendlyAskError(APIError.http(402, "You’ve used this month’s free $3.00."))
    #expect(reached.title == "This month’s limit is reached")
    #expect(reached.detail == "You’ve used this month’s free $3.00.")
  }
}
