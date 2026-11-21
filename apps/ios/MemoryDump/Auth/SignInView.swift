import SwiftUI

/// Sign in or create an account. The server is the one the app was built for.
struct SignInView: View {
  @Environment(AppModel.self) private var app
  @State private var creating = false
  @State private var name = ""
  @State private var phone = ""
  @State private var country = DialCode.local
  @State private var email = ""
  @State private var password = ""
  @State private var busy = false
  @State private var error: String?

  private var trimmedName: String { name.trimmingCharacters(in: .whitespaces) }
  private var canSubmit: Bool {
    guard !email.isEmpty, !password.isEmpty else { return false }
    return !creating || (!trimmedName.isEmpty && !DialCode.digits(phone).isEmpty)
  }

  var body: some View {
    ScrollViewReader { proxy in
      form
        // The notice pushes the button down, under the keyboard on the longer
        // form, and the phone pad has no key that puts it away.
        .onChange(of: error) {
          if error != nil { withAnimation { proxy.scrollTo(Self.submitButton, anchor: .bottom) } }
        }
    }
  }

  private static let submitButton = "submit"

  private var form: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        VStack(alignment: .leading, spacing: 6) {
          Text("Memory Dump").font(.display(36, .heavy, relativeTo: .largeTitle)).foregroundStyle(Palette.ink)
          Text(creating ? "Create your library" : "Sign in to your library")
            .font(.body).foregroundStyle(Palette.ink2)
        }
        .padding(.bottom, 16)

        if creating {
          field("Name") {
            TextField("Jane Doe", text: $name).textContentType(.name)
          }
          field("Phone") {
            PhoneField(country: $country, phone: $phone)
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
        .id(Self.submitButton)

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
        guard let number = country.number(phone) else {
          error = DialCode.refusal
          Haptics.error()
          return
        }
        try await app.signUp(email: email, password: password, name: trimmedName, phone: number)
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
