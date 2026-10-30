import SwiftUI

/// Sign in or create an account. The server is the one the app was built for.
struct SignInView: View {
  @Environment(AppModel.self) private var app
  @State private var creating = false
  @State private var name = ""
  @State private var email = ""
  @State private var password = ""
  @State private var busy = false
  @State private var error: String?

  private var canSubmit: Bool { !email.isEmpty && !password.isEmpty }

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        VStack(alignment: .leading, spacing: 6) {
          Text("Memory Dump").font(.display(36, .heavy, relativeTo: .largeTitle)).foregroundStyle(Palette.ink)
          Text(creating ? "Create your library" : "Sign in to your library")
            .font(.body).foregroundStyle(Palette.ink2)
        }
        .padding(.bottom, 16)

        if creating {
          field("Name (optional)") {
            TextField("Jane Doe", text: $name).textContentType(.name)
          }
        }
        field("Email") {
          TextField(verbatim("you@example.com"), text: $email)
            .textContentType(.emailAddress)
            .keyboardType(.emailAddress)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
        }
        field("Password") {
          SecureField(creating ? "At least 10 characters" : "Your password", text: $password)
            .textContentType(creating ? .newPassword : .password)
        }

        if let error {
          ErrorNotice(title: creating ? "Couldn’t create your account" : "Couldn’t sign you in", detail: error)
        }

        Button {
          Task { await submit() }
        } label: {
          Group {
            if busy { ProgressView().tint(Palette.onInk) } else { Text(creating ? "Create account" : "Sign in") }
          }
          .font(.headline)
          .foregroundStyle(Palette.onInk)
          .frame(maxWidth: .infinity, minHeight: 50)
        }
        .buttonStyle(.borderedProminent)
        .tint(Palette.ink)
        .disabled(!canSubmit)
        .padding(.top, 8)

        HStack(spacing: 4) {
          Text(creating ? "Already have an account?" : "New to Memory Dump?").foregroundStyle(Palette.ink2)
          Button(creating ? "Sign in" : "Create an account") {
            creating.toggle()
            error = nil
          }
          .foregroundStyle(Palette.accent)
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
      }
      .padding(24)
      .padding(.top, 40)
    }
    .scrollDismissesKeyboard(.interactively)
    .background(Palette.bg)
  }

  private func field<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(label).font(.caption).foregroundStyle(Palette.ink3)
      content().font(.body)
    }
    .padding(.horizontal, 14)
    .padding(.vertical, 10)
    .background(Palette.fill, in: .rect(cornerRadius: 12))
  }

  private func submit() async {
    guard !busy else { return }
    busy = true
    error = nil
    defer { busy = false }
    do {
      if creating {
        try await app.signUp(
          email: email, password: password,
          displayName: name.trimmingCharacters(in: .whitespaces).nilIfEmpty)
      } else {
        try await app.signIn(email: email, password: password)
      }
    } catch is URLError {
      error = "Can’t reach \(Format.domain(Server.url.absoluteString)). Check your connection and try again."
    } catch {
      self.error = error.localizedDescription
    }
    if error != nil { Haptics.error() }
  }
}
