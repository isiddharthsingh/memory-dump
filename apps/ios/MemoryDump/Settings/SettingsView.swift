import SwiftUI

struct SettingsView: View {
  @Environment(AppModel.self) private var app
  @Environment(\.dismiss) private var dismiss
  @Environment(\.openURL) private var openURL
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
        }

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

        Section {
          Button("Sign out", role: .destructive) { confirmSignOut = true }
          Button("Delete account…", role: .destructive) { confirmDelete = true }
        } footer: {
          Text("Deleting removes your account, saved items, wiki pages and devices. It can’t be undone.")
        }

        Section {
        } footer: {
          Text("Version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
            .frame(maxWidth: .infinity)
        }
      }
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
