import SwiftUI

/// The account's name and phone number, to change. The email is how the
/// account signs in, so it only shows here.
struct ProfileView: View {
  @Environment(AppModel.self) private var app
  @Environment(\.dismiss) private var dismiss
  /// The account the fields were last filled from.
  @State private var loaded: User
  @State private var name: String
  @State private var country: DialCode
  @State private var phone: String
  @State private var saving = false
  @State private var error: String?

  init(user: User) {
    let number = Self.field(user.phone)
    _loaded = State(initialValue: user)
    _name = State(initialValue: user.displayName ?? "")
    _country = State(initialValue: number.country)
    _phone = State(initialValue: number.text)
  }

  /// A saved number as the phone field shows it. One that can't be read is
  /// shown whole, with its `+`.
  private static func field(_ saved: String?) -> (country: DialCode, text: String) {
    guard let saved else { return (.local, "") }
    if let split = DialCode.split(saved) { return (split.country, split.national) }
    return (.local, saved)
  }

  private var trimmedName: String { name.trimmingCharacters(in: .whitespaces) }
  private var hasPhone: Bool { !DialCode.digits(phone).isEmpty }
  private var nameChanged: Bool { trimmedName != (loaded.displayName ?? "") }
  /// Whether the phone field still shows what it was filled with. The saved
  /// number is then left alone, one the library can't read included.
  private var phoneUntouched: Bool {
    let number = Self.field(loaded.phone)
    return country == number.country && phone == number.text
  }
  private var phoneChanged: Bool { !phoneUntouched && hasPhone && country.number(phone) != loaded.phone }
  /// Whether the fields still show what they were filled with.
  private var untouched: Bool { name == (loaded.displayName ?? "") && phoneUntouched }
  /// A name is needed, and a number can be replaced but not removed.
  private var canSave: Bool {
    !saving && !trimmedName.isEmpty && (hasPhone || loaded.phone == nil) && (nameChanged || phoneChanged)
  }

  var body: some View {
    Form {
      Section("Name") {
        TextField("Jane Doe", text: $name).textContentType(.name)
      }
      .listRowBackground(Palette.card)

      Section {
        PhoneField(country: $country, phone: $phone)
      } header: {
        Text("Phone")
      } footer: {
        if let error {
          Text(error).foregroundStyle(Palette.failed)
        } else if !hasPhone, loaded.phone != nil {
          Text("A phone number can be replaced, not removed.")
        }
      }
      .listRowBackground(Palette.card)

      Section {
        LabeledContent("Email", value: loaded.email)
      } footer: {
        Text("You sign in with your email. It can’t be changed here.")
      }
      .listRowBackground(Palette.card)
    }
    .scrollContentBackground(.hidden)
    .background(Palette.ground)
    .scrollDismissesKeyboard(.interactively)
    .navigationTitle("Profile")
    .navigationBarTitleDisplayMode(.inline)
    // A save under way ends on this screen, where its error would show.
    .navigationBarBackButtonHidden(saving)
    .toolbar {
      ToolbarItem(placement: .confirmationAction) {
        Button("Save") { Task { await save() } }.disabled(!canSave)
      }
    }
    .task { await refresh() }
  }

  /// The session holds the account as it was at sign-in, so ask the server.
  /// What it says replaces the fields only while they are as they were filled.
  private func refresh() async {
    await app.refreshProfile()
    guard let fresh = app.session?.user, fresh != loaded else { return }
    let fill = untouched
    loaded = fresh
    guard fill else { return }
    let number = Self.field(fresh.phone)
    name = fresh.displayName ?? ""
    country = number.country
    phone = number.text
  }

  private func save() async {
    guard !saving else { return }
    var number: String?
    if hasPhone, !phoneUntouched {
      number = country.number(phone)
      guard number != nil else {
        error = DialCode.refusal
        Haptics.error()
        return
      }
    }
    saving = true
    error = nil
    defer { saving = false }
    do {
      // Nil for a number that wasn't touched or reads as the saved one.
      try await app.updateProfile(
        name: nameChanged ? trimmedName : nil, phone: number == loaded.phone ? nil : number)
      Haptics.success()
      dismiss()
    } catch is URLError {
      error = "Can’t reach \(Format.domain(Server.url.absoluteString)). Check your connection and try again."
    } catch {
      self.error = error.localizedDescription
    }
    if error != nil { Haptics.error() }
  }
}
