import SwiftUI

/// Picks the country a phone number belongs to, by name or by code.
struct DialCodePicker: View {
  @Binding var selection: DialCode
  @Environment(\.dismiss) private var dismiss
  @State private var query = ""

  var body: some View {
    NavigationStack {
      List(DialCode.matching(query)) { country in
        Button {
          selection = country
          dismiss()
        } label: {
          HStack(spacing: 12) {
            Text(verbatim: country.flag)
            Text(verbatim: country.name).foregroundStyle(Palette.ink)
            Spacer()
            Text(verbatim: "+\(country.code)").foregroundStyle(Palette.ink2).monospacedDigit()
            Image(systemName: "checkmark")
              .foregroundStyle(Palette.accent)
              .opacity(country == selection ? 1 : 0)
          }
        }
        .accessibilityAddTraits(country == selection ? .isSelected : [])
      }
      .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Country or code")
      .navigationTitle("Country code")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
      }
    }
  }
}
