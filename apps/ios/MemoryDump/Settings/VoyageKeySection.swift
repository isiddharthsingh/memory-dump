import SwiftUI

/// Models & usage › Search: Voyage indexes and searches the library for every
/// account, on Memory Dump's key unless the user saves a Voyage key of their
/// own. On their own models they get a small free amount of Memory Dump's a
/// month, which this shows, and their own key lifts it.
struct VoyageKeySection: View {
  @Binding var settings: AISettings?
  let voyage: AISettings.Voyage
  let usage: AISettings.Usage
  /// A key is being checked or removed. The screen's own, so nothing else on
  /// it sends a change meanwhile: each answer replaces the whole of `settings`.
  @Binding var working: Bool
  /// The key field is open: to add a key, or to replace the saved one.
  @State private var entering = false
  @State private var key = ""
  @State private var keyError: String?
  @State private var confirmRemove = false
  @State private var removeError: String?

  private var trimmedKey: String { key.trimmingCharacters(in: .whitespacesAndNewlines) }

  var body: some View {
    Section {
      if entering {
        // No content type: as a password, iOS would offer to save it.
        SecureField(voyage.placeholder, text: $key)
          .textInputAutocapitalization(.never)
          .autocorrectionDisabled()
        if let keyError { Text(keyError).font(.footnote).foregroundStyle(Palette.failed) }
        Button(working ? "Checking…" : "Save key") { Task { await save() } }
          .disabled(working || trimmedKey.isEmpty)
        Button("Cancel") { close() }.disabled(working)
      } else if let saved = voyage.key {
        LabeledContent("\(voyage.name) key", value: "•••• \(saved.hint)")
        Button("Replace key") { entering = true }.disabled(working)
        // On this row, not the section: a section hands its modifiers to
        // every row, and each would present its own copy.
        Button("Remove key", role: .destructive) { confirmRemove = true }
          .disabled(working)
          .confirmationDialog(
            "Remove your \(voyage.name) key?", isPresented: $confirmRemove, titleVisibility: .visible
          ) {
            Button("Remove key", role: .destructive) { Task { await remove() } }
          } message: {
            Text("The server deletes it, and search goes back to Memory Dump’s \(voyage.name) key.")
          }
          .alert("Couldn’t remove the key", isPresented: .constant(removeError != nil)) {
            Button("OK") { removeError = nil }
          } message: {
            Text(removeError ?? "")
          }
      } else {
        if !usage.text.isEmpty {
          VStack(alignment: .leading, spacing: 10) {
            Text(usage.text)
            if let fraction = usage.fraction {
              ProgressView(value: fraction).tint(usage.isUsedUp ? Palette.failed : Palette.accent)
            }
          }
          .padding(.vertical, 4)
        }
        Button("Use your own \(voyage.name) key") { entering = true }
      }
    } header: {
      Text("Search")
    } footer: {
      Text(usage.footer)
    }
    .listRowBackground(Palette.card)
  }

  private func close() {
    key = ""
    keyError = nil
    entering = false
  }

  /// The server checks the key with Voyage before it keeps it. What it says
  /// about a key it refuses goes under the field.
  private func save() async {
    working = true
    keyError = nil
    defer { working = false }
    do {
      settings = try await APIClient.shared.saveKey(provider: voyage.id, key: trimmedKey, region: nil)
      Haptics.success()
      close()
    } catch {
      Haptics.error()
      keyError = error.localizedDescription
    }
  }

  private func remove() async {
    working = true
    defer { working = false }
    do {
      settings = try await APIClient.shared.removeKey(provider: voyage.id)
    } catch {
      Haptics.error()
      removeError = error.localizedDescription
    }
  }
}

extension AISettings {
  /// What "Search" says of Voyage; nil when there is nothing to offer: the
  /// server says nothing of it or keeps no keys, or the account is on Memory
  /// Dump's own models with no Voyage key, where Voyage is part of the free
  /// amount.
  var voyageUsage: Usage? {
    guard keys, let voyage, inUse != nil || voyage.key != nil else { return nil }
    let what = "\(voyage.name) indexes and searches what you save."
    if voyage.key != nil {
      return Usage(text: "", footer: "\(what) Your key is kept encrypted on the server and never shown again.")
    }
    guard let limit = voyage.limit, limit > 0 else {
      return Usage(text: "", footer: "\(what) It runs on Memory Dump’s key. \(voyage.help)")
    }
    let resets = Format.day(free.resets)
    if voyage.spent >= limit {
      return Usage(
        text: "You’ve used this month’s free \(Format.money(limit)) of \(voyage.name). Add your own key, or wait until \(resets).",
        fraction: 1, isUsedUp: true, footer: "\(what) \(voyage.help)")
    }
    return Usage(
      text: "\(Format.money(voyage.spent)) of \(Format.money(limit)) free on Memory Dump’s \(voyage.name) key",
      fraction: max(0, voyage.spent / limit),
      footer: "\(what) Resets \(resets). With your own key there’s no cap. \(voyage.help)")
  }
}
