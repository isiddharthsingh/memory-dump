import SwiftUI

struct SettingsView: View {
  @Environment(AppModel.self) private var app
  @Environment(\.dismiss) private var dismiss
  @Environment(\.openURL) private var openURL
  @AppStorage(Appearance.key) private var appearance = Appearance.system
  @State private var server = ""
  @State private var health: Bool?
  @State private var checking = false
  @State private var queued = 0
  @State private var flushing = false
  @State private var confirmDiscard = false
  @State private var confirmSignOut = false
  @State private var confirmDelete = false
  @State private var deleteError: String?

  var body: some View {
    NavigationStack {
      Form {
        if let user = app.session?.user {
          Section {
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
          .listRowBackground(Palette.card)
        }

        Section {
          AppearancePicker(selection: $appearance)
        } header: {
          Text("Appearance")
        } footer: {
          Text("System follows your iPhone’s setting.")
        }
        .listRowBackground(Palette.card)

        Section {
          LabeledContent("Address") {
            TextField(verbatim("https://memory.example.com"), text: $server)
              .keyboardType(.URL)
              .textInputAutocapitalization(.never)
              .autocorrectionDisabled()
              .multilineTextAlignment(.trailing)
              .onSubmit { saveServer() }
          }
          LabeledContent("Status") {
            Text(statusLabel.text).foregroundStyle(statusLabel.color)
          }
          Button("Test connection") { Task { await check() } }
        } header: {
          Text("Server")
        } footer: {
          Text("Memory Dump is self-hosted. Point the app at your own server.")
        }
        .listRowBackground(Palette.card)

        Section("Saving") {
          LabeledContent("Offline queue", value: queued == 0 ? "Empty" : Format.plural(queued, "share"))
          if queued > 0 {
            Button(flushing ? "Sending…" : "Send now") { Task { await sendQueued() } }.disabled(flushing)
            Button("Discard queued shares", role: .destructive) { confirmDiscard = true }
          }
          Button("Notifications") {
            if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
          }
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
      .toolbar {
        ToolbarItem(placement: .confirmationAction) {
          Button("Done") {
            saveServer()
            dismiss()
          }
        }
      }
      .confirmationDialog("Discard \(Format.plural(queued, "queued share"))?", isPresented: $confirmDiscard, titleVisibility: .visible) {
        Button("Discard", role: .destructive) {
          ShareQueue.clear()
          queued = ShareQueue.count
        }
      } message: {
        Text("They haven’t reached your server yet.")
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
    }
    .task {
      server = app.serverURL?.absoluteString ?? ""
      queued = ShareQueue.count
      await check()
    }
  }

  private var statusLabel: (text: String, color: Color) {
    if server.isEmpty { return ("Not set", Palette.ink2) }
    if checking { return ("Checking…", Palette.ink2) }
    switch health {
    case true?: return ("Connected", Palette.ready)
    case false?: return ("Unreachable", Palette.failed)
    case nil: return ("", Palette.ink2)
    }
  }

  private func saveServer() {
    guard server != app.serverURL?.absoluteString else { return }
    app.setServer(server)
  }

  private func check() async {
    saveServer()
    checking = true
    health = await APIClient.shared.health()
    checking = false
    health == true ? Haptics.success() : Haptics.error()
  }

  private func sendQueued() async {
    flushing = true
    let result = await ShareQueue.flush()
    flushing = false
    queued = ShareQueue.count
    if result.refused > 0 {
      app.show("\(Format.plural(result.refused, "share")) couldn’t be sent and were removed.", ok: false)
    } else if result.sent > 0 {
      app.show("Sent \(Format.plural(result.sent, "queued share")).", ok: true)
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
