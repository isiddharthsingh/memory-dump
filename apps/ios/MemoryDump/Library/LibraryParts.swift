import SwiftUI

/// One older save, picked with weight on age, so forgotten items come back.
/// Ported from the Expo app's Rediscover.tsx.
struct Rediscover: View {
  let pool: [ItemSummary]
  @Environment(Router.self) private var router
  @State private var round = 0
  @State private var exclude: UUID?

  var body: some View {
    if pool.count >= 2, let pick {
      Button {
        router.push(.item(pick.id))
      } label: {
        HStack(alignment: .top, spacing: 12) {
          VStack(alignment: .leading, spacing: 4) {
            Text("Rediscover · \(Self.savedLabel(pick.createdAt).lowercased())")
              .font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent)
            Text(pick.title ?? Format.domain(pick.url))
              .font(.cardTitle).foregroundStyle(Palette.ink).lineLimit(2)
            Text(Format.sourceMeta(pick.url, pick.source)).font(.footnote).foregroundStyle(Palette.ink3)
          }
          Spacer(minLength: 0)
          Button("Shuffle", systemImage: "shuffle") {
            Haptics.selection()
            exclude = pick.id
            round += 1
          }
          .labelStyle(.iconOnly)
          .buttonStyle(.bordered)
          .buttonBorderShape(.circle)
          .tint(Palette.ink)
        }
        .padding(16)
        .background(Palette.accentSoft, in: .rect(cornerRadius: 16))
      }
      .buttonStyle(.plain)
      .accessibilityLabel("Rediscover: \(pick.title ?? Format.domain(pick.url))")
    }
  }

  private var pick: ItemSummary? {
    let key = pool.map(\.id.lower).joined(separator: ",") + "#\(round)"
    return Self.pickAgeWeighted(pool, r01: Self.seeded(key), exclude: exclude)
  }

  /// Deterministic 0..1 value from a string (FNV-1a), so the pick is stable.
  static func seeded(_ key: String) -> Double {
    var h: UInt32 = 0x811C_9DC5
    for unit in key.utf16 {
      h ^= UInt32(unit)
      h = h &* 0x0100_0193
    }
    return Double(h) / 4_294_967_296
  }

  /// Weight is age in days plus 1, so brand-new items still have a chance.
  static func pickAgeWeighted(_ items: [ItemSummary], r01: Double, exclude: UUID?) -> ItemSummary? {
    let pool = items.count > 1 && exclude != nil ? items.filter { $0.id != exclude } : items
    guard !pool.isEmpty else { return nil }
    let weights = pool.map { Double(1 + Format.daysAgo($0.createdAt)) }
    var r = r01 * weights.reduce(0, +)
    for (item, w) in zip(pool, weights) {
      r -= w
      if r <= 0 { return item }
    }
    return pool.last
  }

  private static func savedLabel(_ date: Date) -> String {
    let d = Format.daysAgo(date)
    if d == 0 { return "Saved today" }
    if d == 1 { return "Saved yesterday" }
    return "Saved \(Format.plural(d, "day")) ago"
  }
}

struct SearchResultsView: View {
  let query: String
  let hits: [SearchHit]
  let isSearching: Bool
  let error: Error?
  @Environment(Router.self) private var router

  var body: some View {
    if isSearching && hits.isEmpty {
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
    } else if let error {
      ErrorNotice(title: "Search isn’t working right now", detail: error.localizedDescription)
        .padding(20)
        .frame(maxHeight: .infinity, alignment: .top)
    } else if hits.isEmpty {
      ContentUnavailableView.search(text: query)
    } else {
      List(hits, id: \.id) { hit in
        Button {
          router.push(hit.kind == "item" ? .item(hit.id) : .wiki(hit.slug ?? ""))
        } label: {
          HStack(alignment: .top, spacing: 12) {
            if hit.kind == "item" {
              SourceThumb(source: Self.source(of: hit.url), url: hit.url, size: 44)
            } else {
              WikiTile(size: 44)
            }
            VStack(alignment: .leading, spacing: 3) {
              Text(title(of: hit)).font(.headline).foregroundStyle(Palette.ink).lineLimit(2)
              Text(hit.snippet).font(.subheadline).foregroundStyle(Palette.ink2).lineLimit(2)
            }
          }
          .contentShape(.rect)
        }
        .buttonStyle(.plain)
      }
      .listStyle(.plain)
    }
  }

  private func title(of hit: SearchHit) -> String {
    if hit.kind == "item" { return hit.title ?? hit.url.map(Format.domain) ?? "Saved item" }
    return Format.topicTitle(hit.title ?? hit.slug ?? "")
  }

  private static func source(of url: String?) -> String {
    let host = url.map(Format.domain) ?? ""
    if host.contains(/(^|\.)youtube\.com$|^youtu\.be$/) { return "youtube" }
    if host.contains(/(^|\.)(x|twitter)\.com$/) { return "twitter" }
    return "web"
  }
}

/// Manual capture for links that did not come through the share sheet.
struct SaveLinkSheet: View {
  let onSaved: () async -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var text = ""
  @State private var saving = false
  @State private var error: String?
  @FocusState private var focused: Bool

  var body: some View {
    NavigationStack {
      VStack(alignment: .leading, spacing: 12) {
        Text("Paste any article, video or post. It’s summarized and filed for you.")
          .font(.subheadline).foregroundStyle(Palette.ink2)
        TextField(verbatim("https://"), text: $text)
          .keyboardType(.URL)
          .textInputAutocapitalization(.never)
          .autocorrectionDisabled()
          .focused($focused)
          .submitLabel(.done)
          .onSubmit { Task { await save() } }
          .padding(14)
          .background(Palette.fill, in: .rect(cornerRadius: 12))
        if let error { Text(error).font(.footnote).foregroundStyle(Palette.failed) }
        Spacer()
      }
      .padding(20)
      .navigationTitle("Save a link")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
        ToolbarItem(placement: .confirmationAction) {
          Button("Save to Library") { Task { await save() } }
            .disabled(Self.normalize(text) == nil || saving)
        }
      }
    }
    .presentationDetents([.height(240)])
    .onAppear { focused = true }
  }

  private func save() async {
    guard let url = Self.normalize(text), !saving else { return }
    saving = true
    defer { saving = false }
    do {
      _ = try await APIClient.shared.ingest(url, note: nil)
      Haptics.success()
      await onSaved()
      dismiss()
    } catch {
      self.error = "Couldn’t save: \(error.localizedDescription)"
    }
  }

  static func normalize(_ input: String) -> URL? {
    let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    let withScheme = trimmed.contains(/^[a-zA-Z]+:\/\//) ? trimmed : "https://\(trimmed)"
    guard withScheme.contains(/^(?i)https?:\/\/[^\s\/]+\.[^\s]+/) else { return nil }
    return URL(string: withScheme)
  }
}

/// Edits the note saved with an item (`share_text`). Saving re-embeds the item.
struct NoteEditor: View {
  let itemId: UUID
  let title: String
  let onSaved: () async -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var text = ""
  @State private var loaded = false
  @State private var saving = false
  @State private var error: String?
  @FocusState private var focused: Bool

  var body: some View {
    NavigationStack {
      VStack(alignment: .leading, spacing: 10) {
        Text(title).font(.subheadline).foregroundStyle(Palette.ink2).lineLimit(2)
        TextEditor(text: $text)
          .focused($focused)
          .scrollContentBackground(.hidden)
          .padding(10)
          .background(Palette.fill, in: .rect(cornerRadius: 12))
          .overlay(alignment: .topLeading) {
            if text.isEmpty {
              Text("Key idea, follow-up, who mentioned it…")
                .foregroundStyle(Palette.ink3)
                .padding(.horizontal, 15)
                .padding(.vertical, 18)
                .allowsHitTesting(false)
            }
          }
        if let error { Text(error).font(.footnote).foregroundStyle(Palette.failed) }
      }
      .padding(20)
      .navigationTitle("Your note")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
        ToolbarItem(placement: .confirmationAction) {
          Button("Save note") { Task { await save() } }.disabled(!loaded || saving)
        }
      }
    }
    .presentationDetents([.medium, .large])
    .task {
      text = (try? await APIClient.shared.item(itemId))?.shareText ?? ""
      loaded = true
      focused = true
    }
  }

  private func save() async {
    saving = true
    defer { saving = false }
    do {
      let note = text.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
      _ = try await APIClient.shared.updateNote(itemId, note: note)
      Haptics.success()
      await onSaved()
      dismiss()
    } catch {
      self.error = error.localizedDescription
    }
  }
}
