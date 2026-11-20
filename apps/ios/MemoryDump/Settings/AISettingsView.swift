import SwiftUI

/// Settings › Models & usage: what this month has cost, whose models do the
/// work (Memory Dump's, or a provider the user saved their own API key for),
/// which of that provider's models, and a limit on what the key may spend.
/// `settings` is Settings' copy, so its row says what changed here.
struct AISettingsView: View {
  @Binding var settings: AISettings?
  @State private var loadError: String?
  /// A provider picked in the menu that has no key yet. Nothing changes on
  /// the server until its key is saved.
  @State private var pending: String?
  @State private var key = ""
  @State private var region = Self.defaultRegion
  @State private var replacing = false
  @State private var checking = false
  @State private var keyError: String?
  @State private var confirmRemove = false
  /// The toggle, which can be on before an amount is set.
  @State private var limitOn = false
  @State private var limitText = ""
  @FocusState private var limitFocused: Bool
  /// A change other than a key is on its way.
  @State private var busy = false
  /// The Search section is checking or removing a Voyage key.
  @State private var voyageWorking = false
  @State private var error: String?

  private static let defaultRegion = "us-east-1"

  /// The provider the menu shows: the one waiting for a key, or the one in use.
  private var shown: AISettings.Provider? {
    settings?.providers.first { $0.id == pending ?? settings?.provider }
  }

  /// Another change is on its way. Each one's answer replaces the whole of
  /// `settings`, so they go one at a time.
  private var locked: Bool { busy || voyageWorking }

  private var trimmedKey: String { key.trimmingCharacters(in: .whitespacesAndNewlines) }
  private var trimmedRegion: String { region.trimmingCharacters(in: .whitespacesAndNewlines) }

  var body: some View {
    Group {
      if let settings {
        form(settings)
      } else if let loadError {
        ErrorNotice(title: "Couldn’t load your models and usage", detail: loadError) { Task { await load() } }
          .padding(20)
          .frame(maxHeight: .infinity, alignment: .top)
      } else {
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
      }
    }
    .background(Palette.ground)
    .navigationTitle("Models & usage")
    .navigationBarTitleDisplayMode(.inline)
    .task { await load() }
    .onChange(of: settings?.own.limit, initial: true) { _, limit in
      limitOn = limit != nil
      limitText = limit.map { String(Format.money($0, short: true).dropFirst()) } ?? ""
    }
    .confirmationDialog(
      "Remove your \(shown?.name ?? "") key?", isPresented: $confirmRemove, titleVisibility: .visible
    ) {
      Button("Remove key", role: .destructive) { removeKey() }
    } message: {
      Text("The server deletes it, and Memory Dump goes back to its own models.")
    }
    .alert("Couldn’t save that change", isPresented: .constant(error != nil)) {
      Button("OK") { error = nil }
    } message: {
      Text(error ?? "")
    }
  }

  private func form(_ ai: AISettings) -> some View {
    Form {
      usageSection(ai.usage)
      if ai.keys {
        providerSection(ai)
        if let provider = ai.inUse, pending == nil { modelsSection(provider) }
        // The limit is on every key of the user's own, a Voyage key alone too.
        if pending == nil, ai.inUse != nil || ai.voyage?.key != nil { limitSection(ai) }
        if pending == nil, let voyage = ai.voyage, let usage = ai.voyageUsage {
          VoyageKeySection(settings: $settings, voyage: voyage, usage: usage, working: $voyageWorking)
            .disabled(busy || checking)
        }
      }
    }
    .scrollContentBackground(.hidden)
    .scrollDismissesKeyboard(.interactively)
  }

  // MARK: This month

  private func usageSection(_ usage: AISettings.Usage) -> some View {
    Section {
      VStack(alignment: .leading, spacing: 10) {
        Text(usage.text)
        if let fraction = usage.fraction {
          ProgressView(value: fraction).tint(usage.isUsedUp ? Palette.failed : Palette.accent)
        }
      }
      .padding(.vertical, 4)
    } header: {
      Text("This month")
    } footer: {
      if !usage.footer.isEmpty { Text(usage.footer) }
    }
    .listRowBackground(Palette.card)
  }

  // MARK: Models from

  private func providerSection(_ ai: AISettings) -> some View {
    Section {
      Picker("Provider", selection: Binding(get: { pending ?? ai.provider }, set: { pick($0) })) {
        Text("Memory Dump").tag(String?.none)
        ForEach(ai.providers) { Text($0.name).tag(String?.some($0.id)) }
      }
      .pickerStyle(.menu)
      .disabled(locked || checking)
      if let provider = shown {
        if let saved = provider.key, !replacing {
          LabeledContent("Key", value: "•••• \(saved.hint)")
          if provider.region, let region = saved.region { LabeledContent("Region", value: region) }
          Button("Replace key") {
            clearKeyForm()
            region = saved.region ?? Self.defaultRegion
            replacing = true
          }
          Button("Remove key", role: .destructive) { confirmRemove = true }.disabled(locked)
        } else {
          keyForm(provider)
        }
      }
    } header: {
      Text("Models from")
    } footer: {
      Text(providerFooter(ai))
    }
    .listRowBackground(Palette.card)
  }

  @ViewBuilder private func keyForm(_ provider: AISettings.Provider) -> some View {
    // No content type: as a password, iOS would offer to save it.
    SecureField(provider.placeholder, text: $key)
      .textInputAutocapitalization(.never)
      .autocorrectionDisabled()
    if provider.region {
      LabeledContent("Region") {
        TextField(verbatim(Self.defaultRegion), text: $region)
          .multilineTextAlignment(.trailing)
          .textInputAutocapitalization(.never)
          .autocorrectionDisabled()
      }
    }
    if let keyError { Text(keyError).font(.footnote).foregroundStyle(Palette.failed) }
    Button(checking ? "Checking…" : "Save key") { Task { await saveKey(provider) } }
      .disabled(checking || voyageWorking || trimmedKey.isEmpty || (provider.region && trimmedRegion.isEmpty))
    if replacing { Button("Cancel") { clearKeyForm() }.disabled(checking) }
  }

  private func providerFooter(_ ai: AISettings) -> String {
    guard let shown else { return "Pick a provider to use your own API key." }
    var lines = [shown.help, "Kept encrypted on the server and never shown again."]
    if shown.key == nil, let inUse = ai.inUse {
      lines.append("Your \(inUse.name) key stays in use until this one is saved.")
    }
    return lines.joined(separator: " ")
  }

  // MARK: Models

  private func modelsSection(_ provider: AISettings.Provider) -> some View {
    Section("Models") {
      ForEach(AISettings.Job.allCases) { modelPicker($0, provider) }
    }
    .listRowBackground(Palette.card)
    .disabled(locked)
  }

  private func modelPicker(_ job: AISettings.Job, _ provider: AISettings.Provider) -> some View {
    let chosen = provider.chosen[job]
    return Picker(job.title, selection: Binding(get: { chosen }, set: { choose($0, for: job, provider) })) {
      ForEach(provider.options(including: chosen)) { Text($0.name).tag($0.id) }
    }
    .pickerStyle(.menu)
  }

  // MARK: Spend limit

  private func limitSection(_ ai: AISettings) -> some View {
    Section {
      Toggle("Monthly limit", isOn: Binding(get: { limitOn }, set: { toggleLimit($0) }))
        .tint(Palette.accent)
      if limitOn {
        HStack(spacing: 6) {
          Text(verbatim("$")).foregroundStyle(Palette.ink2)
          TextField(verbatim("10"), text: $limitText)
            .keyboardType(.decimalPad)
            .focused($limitFocused)
            .onSubmit { setLimit() }
          // Borderless, or a tap anywhere on the row would press it.
          Button("Set") { setLimit() }
            .buttonStyle(.borderless)
            .disabled(AISettings.amount(limitText).map { $0 == ai.own.limit } ?? true)
        }
      }
    } header: {
      Text("Spend limit")
    } footer: {
      Text("When this month’s spend on your key reaches it, Memory Dump stops using the key until next month.")
    }
    .listRowBackground(Palette.card)
    .disabled(locked)
  }

  // MARK: Loading and changing

  /// The saved copy first, then the server's.
  private func load() async {
    if settings == nil { settings = try? await APIClient.saved.aiSettings() }
    let before = settings
    do {
      let fresh = try await APIClient.shared.aiSettings()
      // Not over a change made meanwhile: its answer is newer than this one.
      if settings == before { settings = fresh }
      loadError = nil
    } catch {
      if settings == nil { loadError = error.localizedDescription }
    }
  }

  /// Sends a change and shows the settings the server answers with.
  /// `optimistic` is shown until then, so a menu keeps what was picked; a
  /// failure puts back what was there and says why.
  @discardableResult
  private func write(
    showing optimistic: AISettings? = nil, _ request: () async throws -> AISettings
  ) async -> Bool {
    let before = settings
    if let optimistic { settings = optimistic }
    busy = true
    defer { busy = false }
    do {
      settings = try await request()
      return true
    } catch {
      settings = before
      Haptics.error()
      self.error = error.localizedDescription
      return false
    }
  }

  private func pick(_ id: String?) {
    guard let settings, id != pending ?? settings.provider else { return }
    Haptics.selection()
    clearKeyForm()
    // One with no key waits for it in the form.
    if let id, settings.providers.first(where: { $0.id == id })?.key == nil {
      pending = id
      return
    }
    pending = nil
    guard id != settings.provider else { return }
    var next = settings
    next.provider = id
    Task { await write(showing: next) { try await APIClient.shared.useProvider(id) } }
  }

  private func clearKeyForm() {
    key = ""
    region = Self.defaultRegion
    keyError = nil
    replacing = false
  }

  /// The server checks the key with the provider before it keeps it, and
  /// then uses that provider. What it says about a key it refuses goes under
  /// the field.
  private func saveKey(_ provider: AISettings.Provider) async {
    checking = true
    keyError = nil
    defer { checking = false }
    do {
      settings = try await APIClient.shared.saveKey(
        provider: provider.id, key: trimmedKey, region: provider.region ? trimmedRegion : nil)
      Haptics.success()
      clearKeyForm()
      pending = nil
    } catch {
      Haptics.error()
      keyError = error.localizedDescription
    }
  }

  private func removeKey() {
    guard let provider = shown else { return }
    Task { await write { try await APIClient.shared.removeKey(provider: provider.id) } }
  }

  private func choose(_ model: String, for job: AISettings.Job, _ provider: AISettings.Provider) {
    guard var next = settings, let i = next.providers.firstIndex(where: { $0.id == provider.id }),
      next.providers[i].chosen[job] != model
    else { return }
    Haptics.selection()
    next.providers[i].chosen[job] = model
    let chosen = next.providers[i].chosen
    Task {
      await write(showing: next) {
        try await APIClient.shared.setModels(
          provider: provider.id, analysis: chosen.analysis, synthesis: chosen.synthesis, ask: chosen.ask)
      }
    }
  }

  /// Turning it on only shows the amount field; turning it off removes the limit.
  private func toggleLimit(_ on: Bool) {
    limitOn = on
    guard !on, var next = settings, next.own.limit != nil else { return }
    limitFocused = false
    next.own.limit = nil
    Task { await write(showing: next) { try await APIClient.shared.setSpendLimit(nil) } }
  }

  private func setLimit() {
    guard !locked, let amount = AISettings.amount(limitText), amount != settings?.own.limit else { return }
    limitFocused = false
    Task {
      if await write({ try await APIClient.shared.setSpendLimit(amount) }) { Haptics.success() }
    }
  }
}

// MARK: What the screen says

extension AISettings {
  /// The provider whose saved key is in use; nil on Memory Dump's own models.
  var inUse: Provider? { providers.first { $0.id == provider } }

  /// The free amount for the month; nil when this account has no cap.
  private var freeLimit: Double? { admin ? nil : free.limit }

  /// The value on Settings' row: "$0.42 of $3 free", "No limit", or "Your
  /// OpenAI key".
  var summary: String {
    if let inUse { return "Your \(inUse.name) key" }
    guard let limit = freeLimit else { return "No limit" }
    return "\(Format.money(min(free.spent, limit))) of \(Format.money(limit, short: true)) free"
  }

  /// What "This month" shows.
  struct Usage: Equatable {
    var text: String
    /// How full the bar is, 0 to 1; nil when there's no limit to fill.
    var fraction: Double?
    /// The limit is reached, so nothing more is spent until the month ends.
    var isUsedUp = false
    var footer = ""
  }

  var usage: Usage {
    let resets = Format.day(free.resets)
    var usage: Usage
    if let inUse {
      let whose = voyage?.key == nil ? "\(inUse.name) key" : "\(inUse.name) and Voyage keys"
      usage = Usage(text: "\(Format.money(own.spent)) on your \(whose) this month")
      if let limit = own.limit, limit > 0 {
        usage.fraction = min(1, max(0, own.spent / limit))
        usage.isUsedUp = own.spent >= limit
        usage.footer =
          usage.isUsedUp
          ? "You’ve reached your \(Format.money(limit)) limit. Memory Dump won’t use the key again until \(resets)."
          : "Of your \(Format.money(limit)) limit. Resets \(resets)."
      } else {
        usage.footer = "Resets \(resets)."
      }
    } else if let limit = freeLimit, free.spent >= limit {
      let wait = keys ? "Add your own key to keep going, or wait until \(resets)." : "It’s free again on \(resets)."
      usage = Usage(text: "You’ve used this month’s free \(Format.money(limit)). \(wait)", fraction: 1, isUsedUp: true)
    } else if let limit = freeLimit {
      usage = Usage(
        text: "\(Format.money(free.spent)) of \(Format.money(limit)) free",
        fraction: limit > 0 ? max(0, free.spent / limit) : nil,
        footer: keys ? "Resets \(resets). With your own key there’s no cap." : "Resets \(resets).")
    } else {
      usage = Usage(text: "\(Format.money(free.spent)) spent this month", footer: "No limit on this account.")
    }
    if !keys {
      usage.footer += (usage.footer.isEmpty ? "" : " ") + "This server isn’t set up to store your own API keys."
    }
    return usage
  }

  /// What was typed for a limit, as dollars; nil unless it's an amount over
  /// zero. A decimal pad types a comma in some regions.
  static func amount(_ text: String) -> Double? {
    let typed = text.trimmingCharacters(in: .whitespaces).replacing(",", with: ".")
    guard let amount = Double(typed), amount.isFinite, amount > 0 else { return nil }
    return amount
  }
}

extension AISettings {
  /// What a provider's models are chosen for, in the order the screen lists them.
  enum Job: CaseIterable, Identifiable {
    case analysis, synthesis, ask

    var id: Self { self }

    var title: String {
      switch self {
      case .analysis: "Reading saves"
      case .synthesis: "Writing the wiki"
      case .ask: "Answering"
      }
    }
  }
}

extension AISettings.Provider.Chosen {
  subscript(job: AISettings.Job) -> String {
    get {
      switch job {
      case .analysis: analysis
      case .synthesis: synthesis
      case .ask: ask
      }
    }
    set {
      switch job {
      case .analysis: analysis = newValue
      case .synthesis: synthesis = newValue
      case .ask: ask = newValue
      }
    }
  }
}

extension AISettings.Provider {
  /// The menu for one job: the provider's models, and the model in use when
  /// the list no longer has it, so the menu always shows what's chosen.
  func options(including chosen: String) -> [Model] {
    models.contains { $0.id == chosen } ? models : models + [Model(id: chosen, name: chosen)]
  }
}
