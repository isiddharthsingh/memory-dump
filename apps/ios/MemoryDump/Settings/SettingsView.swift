import SwiftUI

struct SettingsView: View {
  @Environment(AppModel.self) private var app
  @Environment(\.dismiss) private var dismiss
  @AppStorage(Appearance.key) private var appearance = Appearance.system
  #if DEBUG
    @State private var health: Bool?
    @State private var checking = false
  #endif
  /// Saves the server is still analyzing; nil when it can't be asked.
  @State private var analyzing: Int?
  @State private var confirmDiscard = false
  @State private var confirmSignOut = false
  @State private var confirmDelete = false
  @State private var deleteError: String?
  @State private var exporting = false
  @State private var exported: ExportedFile?
  @State private var exportError: String?
  @State private var confirmWithdraw = false
  @State private var withdrawing = false
  @State private var withdrawError: String?
  /// Whose models are in use and the month's spend; nil until it's known.
  @State private var ai: AISettings?

  var body: some View {
    NavigationStack {
      Form {
        if let user = app.session?.user {
          Section {
            // By value, so the screen (and the phone library its fields
            // need) is made when it is opened, not each time this one draws.
            NavigationLink(value: user) {
              HStack(spacing: 14) {
                Text(Format.initials(user.displayName ?? user.email))
                  .font(.title3.weight(.semibold)).foregroundStyle(Palette.onInk)
                  .frame(width: 56, height: 56)
                  .background(Palette.ink, in: .circle)
                VStack(alignment: .leading, spacing: 2) {
                  Text(user.displayName ?? user.email).font(.headline)
                  if user.displayName != nil { Text(user.email).font(.subheadline).foregroundStyle(Palette.ink2) }
                }
              }
            }
            .accessibilityHint("Edit your name and phone number")
          }
          .listRowBackground(Palette.card)
        }

        Section {
          NavigationLink {
            AISettingsView(settings: $ai)
          } label: {
            LabeledContent("Models & usage", value: ai?.summary ?? "")
          }
        }
        .listRowBackground(Palette.card)

        Section {
          AppearancePicker(selection: $appearance)
        } header: {
          Text("Appearance")
        } footer: {
          Text("System follows your iPhone’s setting.")
        }
        .listRowBackground(Palette.card)

        // Which backend a development build talks to; a release build has one.
        #if DEBUG
          Section("Server") {
            LabeledContent("Address", value: Format.domain(Server.url.absoluteString))
            LabeledContent("Status") {
              Text(statusLabel.text).foregroundStyle(statusLabel.color)
            }
            Button("Test connection") { Task { await check() } }
          }
          .listRowBackground(Palette.card)
        #endif

        Section {
          let queued = app.queued.count
          LabeledContent("Offline queue", value: queued == 0 ? "Empty" : Format.plural(queued, "share"))
          if queued > 0 {
            Button(app.isSendingQueue ? "Sending…" : "Send now") { app.sendQueue() }.disabled(app.isSendingQueue)
            Button("Discard queued shares", role: .destructive) { confirmDiscard = true }
          }
          if let analyzing {
            LabeledContent("Being analyzed", value: analyzing == 0 ? "None" : Format.plural(analyzing, "save"))
          }
          Button("Notifications") {
            // Not through openURL: in the tabs that's the Router, which opens
            // only web and mail links.
            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
          }
        } header: {
          Text("Saving")
        } footer: {
          Text("Shares wait in the offline queue until they can be sent. Memory Dump then analyzes each one.")
        }
        .listRowBackground(Palette.card)

        Section {
          Button(exporting ? "Preparing your export…" : "Export for Obsidian") { Task { await export() } }
            .disabled(exporting)
        } header: {
          Text("Your data")
        } footer: {
          Text("A zip of Markdown notes, one per saved item and wiki page. Unzip it into an Obsidian vault.")
        }
        .listRowBackground(Palette.card)

        Section {
          let consented = app.session?.user.aiConsentAt != nil
          Button {
            confirmWithdraw = true
          } label: {
            LabeledContent("AI processing", value: consented ? "On" : "Off")
          }
          .tint(Palette.ink)
          .disabled(!consented || withdrawing)
          .accessibilityHint(consented ? "Withdraw your consent to AI processing" : "")
          Link("Privacy Policy", destination: PublicPage.privacy)
          Link("Terms of Use", destination: PublicPage.terms)
          Link("Help & Support", destination: PublicPage.support)
        } header: {
          Text("Privacy")
        } footer: {
          Text("Memory Dump sends what you save, search and ask to AI services to read, organize and answer it.")
        }
        .listRowBackground(Palette.card)

        Section {
          Button("Sign out", role: .destructive) { confirmSignOut = true }
          Button("Delete account…", role: .destructive) { confirmDelete = true }
        } footer: {
          Text("Deleting removes your account, saved items, wiki pages and devices. It can’t be undone.")
        }
        .listRowBackground(Palette.card)

        Section {
        } footer: {
          Text("Version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
            .frame(maxWidth: .infinity)
        }
      }
      // The app's ground and cards rather than the system's grey, so it matches in dark.
      .scrollContentBackground(.hidden)
      .background(Palette.ground)
      .navigationTitle("Settings")
      .navigationBarTitleDisplayMode(.inline)
      .navigationDestination(for: User.self) { ProfileView(user: $0) }
      .toolbar {
        ToolbarItem(placement: .confirmationAction) {
          Button("Done") { dismiss() }
        }
      }
      .confirmationDialog("Discard \(Format.plural(app.queued.count, "queued share"))?", isPresented: $confirmDiscard, titleVisibility: .visible) {
        Button("Discard", role: .destructive) { app.discardQueue() }
      } message: {
        Text("They haven’t been sent yet.")
      }
      .confirmationDialog("Withdraw consent?", isPresented: $confirmWithdraw, titleVisibility: .visible) {
        Button("Withdraw", role: .destructive) { Task { await withdraw() } }
      } message: {
        Text(
          "Memory Dump will stop sending your content to AI services. You won’t be able to save or ask anything new until you allow it again."
        )
      }
      .alert("Couldn’t withdraw consent", isPresented: .constant(withdrawError != nil)) {
        Button("OK") { withdrawError = nil }
      } message: {
        Text(withdrawError ?? "")
      }
      .confirmationDialog("Sign out?", isPresented: $confirmSignOut, titleVisibility: .visible) {
        Button("Sign out", role: .destructive) { Task { await app.signOut() } }
      } message: {
        Text("You’ll need to sign in again on this device.")
      }
      .confirmationDialog("Delete account?", isPresented: $confirmDelete, titleVisibility: .visible) {
        Button("Delete account", role: .destructive) { Task { await deleteAccount() } }
      } message: {
        Text("This permanently removes your account and every saved item, wiki page and device. It can’t be undone.")
      }
      .alert("Couldn’t delete your account", isPresented: .constant(deleteError != nil)) {
        Button("OK") { deleteError = nil }
      } message: {
        Text(deleteError ?? "")
      }
      .alert("Couldn’t export your library", isPresented: .constant(exportError != nil)) {
        Button("OK") { exportError = nil }
      } message: {
        Text(exportError ?? "")
      }
      .sheet(item: $exported) { file in
        ShareSheet(file: file.url).presentationDetents([.medium, .large])
      }
    }
    #if DEBUG
      .task { await check() }
    #endif
    .task { await loadAI() }
    // Again when the queue changes: what it just sent is now being analyzed.
    .task(id: app.queued.count) { await countAnalyzing() }
  }

  #if DEBUG
    private var statusLabel: (text: String, color: Color) {
      if checking { return ("Checking…", Palette.ink2) }
      switch health {
      case true?: return ("Connected", Palette.ready)
      case false?: return ("Unreachable", Palette.failed)
      case nil: return ("", Palette.ink2)
      }
    }

    private func check() async {
      checking = true
      health = await APIClient.shared.health()
      checking = false
      health == true ? Haptics.success() : Haptics.error()
    }
  #endif

  /// Takes the consent back. The account the server answers with has none,
  /// so RootView puts ConsentView in place of the app, and this sheet with it.
  private func withdraw() async {
    withdrawing = true
    defer { withdrawing = false }
    do {
      try await app.setAIConsent(false)
      Haptics.success()
    } catch is URLError {
      Haptics.error()
      withdrawError = "Can’t connect to Memory Dump. Check your connection and try again."
    } catch {
      Haptics.error()
      withdrawError = error.localizedDescription
    }
  }

  /// The saved copy first, then the server's. The row says nothing when
  /// there's neither.
  private func loadAI() async {
    if ai == nil { ai = try? await APIClient.saved.aiSettings() }
    let before = ai
    // Not over a change made on the screen meanwhile: its answer is newer.
    if let fresh = try? await APIClient.shared.aiSettings(), ai == before { ai = fresh }
  }

  /// Counts the saves the server has but hasn't finished with, for as long as
  /// Settings is open. The offline queue empties as soon as the server takes
  /// its shares, which is before they are ready.
  private func countAnalyzing() async {
    while !Task.isCancelled {
      let count = try? await Self.busyCount()
      // A newer count has taken over, and this one's failure mustn't hide it.
      guard !Task.isCancelled else { return }
      analyzing = count
      // It keeps asking after a zero or a failure, only slower: the server
      // may be back by then, and a count can miss a save (see `busyCount`).
      try? await Task.sleep(for: .seconds((count ?? 0) > 0 ? 3 : 15))
    }
  }

  /// Asked of the server only and kept off the disk: a saved copy says
  /// nothing about now. The lists are read one after the other, waiting
  /// first, so a save that starts in between is counted twice rather than
  /// missed; one sent back to wait in between is missed until the next count.
  private static func busyCount() async throws -> Int {
    var count = 0
    for status in ItemSummary.busyStatuses {
      count += try await APIClient.shared.listItems(limit: 200, status: status, saves: false).count
    }
    return count
  }

  /// Downloads the export, then offers it to Files and other apps.
  private func export() async {
    exporting = true
    defer { exporting = false }
    do {
      exported = ExportedFile(url: try await APIClient.shared.exportObsidian())
    } catch {
      Haptics.error()
      exportError = error.localizedDescription
    }
  }

  private func deleteAccount() async {
    do {
      try await app.deleteAccount()
    } catch {
      deleteError = error.localizedDescription
    }
  }
}

private struct ExportedFile: Identifiable {
  let url: URL
  var id: URL { url }
}

/// The system share sheet for a file, so the export can go to Files.
private struct ShareSheet: UIViewControllerRepresentable {
  let file: URL

  func makeUIViewController(context: Context) -> UIActivityViewController {
    UIActivityViewController(activityItems: [file], applicationActivities: nil)
  }

  func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// System, Light and Dark, each shown as a tiny Library wall. System shows
/// half of each.
private struct AppearancePicker: View {
  @Binding var selection: Appearance

  var body: some View {
    HStack(spacing: 8) {
      ForEach(Appearance.allCases) { option in
        let selected = selection == option
        Button {
          guard !selected else { return }
          Haptics.selection()
          selection = option
        } label: {
          VStack(spacing: 9) {
            preview(option)
              .overlay {
                RoundedRectangle(cornerRadius: 16)
                  .strokeBorder(selected ? Palette.accent : Palette.hairline, lineWidth: selected ? 2.5 : 1)
              }
            Text(option.label).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.ink)
              .lineLimit(1).minimumScaleFactor(0.7)
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
              .font(.title3)
              .foregroundStyle(selected ? Palette.accent : Palette.hairlineStrong)
          }
          .frame(maxWidth: .infinity)
          .contentShape(.rect)
        }
        // Plain, or the Form row would treat the three as one button.
        .buttonStyle(.plain)
        .accessibilityLabel(option.label)
        .accessibilityAddTraits(selected ? .isSelected : [])
      }
    }
    .padding(.vertical, 8)
  }

  @ViewBuilder private func preview(_ option: Appearance) -> some View {
    switch option {
    case .light: MiniWall().environment(\.colorScheme, .light)
    case .dark: MiniWall().environment(\.colorScheme, .dark)
    case .system:
      MiniWall().environment(\.colorScheme, .light)
        .overlay {
          MiniWall().environment(\.colorScheme, .dark)
            .mask { HStack(spacing: 0) { Color.clear; Color.black } }
        }
    }
  }
}

/// A Library wall the size of a thumbnail, drawn from the palette, so it
/// shows whichever mode its environment asks for. Laid out on an 88-point
/// grid and scaled down when the row is narrower than three of them.
private struct MiniWall: View {
  var body: some View {
    GeometryReader { geo in
      let s = geo.size.width / 88
      ZStack(alignment: .topLeading) {
        Palette.ground
        Capsule().fill(Palette.ink).frame(width: 34 * s, height: 7 * s).offset(x: 9 * s, y: 11 * s)
        card(Palette.card, x: 9, y: 25, height: 40, scale: s)
        card(Swatch.butter.fill, x: 9, y: 70, height: 36, scale: s)
        card(Palette.video, x: 46, y: 25, height: 28, scale: s)
        card(Palette.card, x: 46, y: 58, height: 48, scale: s)
      }
    }
    .aspectRatio(88.0 / 116, contentMode: .fit)
    .frame(maxWidth: 88)
    .clipShape(.rect(cornerRadius: 16))
    .accessibilityHidden(true)
  }

  private func card(_ color: Color, x: CGFloat, y: CGFloat, height: CGFloat, scale s: CGFloat) -> some View {
    RoundedRectangle(cornerRadius: 7 * s).fill(color).frame(width: 33 * s, height: height * s).offset(x: x * s, y: y * s)
  }
}
