import SwiftUI

/// A phone number as its country, picked from every country's code, and the
/// number typed after it. `DialCode.number` reads the two.
struct PhoneField: View {
  @Binding var country: DialCode
  @Binding var phone: String
  @State private var picking = false

  var body: some View {
    HStack(spacing: 10) {
      Button {
        picking = true
      } label: {
        HStack(spacing: 4) {
          Text(verbatim: "\(country.flag) +\(country.code)")
          Image(systemName: "chevron.down").font(.caption2.weight(.semibold))
        }
        .foregroundStyle(Palette.ink)
      }
      // In a Form the row's tap would otherwise be the button's.
      .buttonStyle(.borderless)
      // A number typed with its own code is sent as it is.
      .opacity(DialCode.hasOwnCode(phone) ? 0.35 : 1)
      .accessibilityLabel("Country code")
      .accessibilityValue(Text(verbatim: "\(country.name), +\(country.code)"))
      TextField("Phone number", text: $phone)
        .textContentType(.telephoneNumber)
        .keyboardType(.phonePad)
    }
    .sheet(isPresented: $picking) { DialCodePicker(selection: $country) }
  }
}
