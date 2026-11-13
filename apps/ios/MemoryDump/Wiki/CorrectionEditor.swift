import SwiftUI

/// Says what a wiki page gets wrong. The server keeps it with the page and
/// rewrites the page to agree.
struct CorrectionEditor: View {
  let title: String
  let send: (String) async throws -> Void
  @Environment(\.dismiss) private var dismiss
  @State private var text = ""
  @State private var sending = false
  @State private var error: String?
  @FocusState private var focused: Bool

  private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

  var body: some View {
    NavigationStack {
      VStack(alignment: .leading, spacing: 10) {
        Text("What does “\(title)” get wrong? The page is rewritten to agree, and stays that way as you save more.")
          .font(.subheadline).foregroundStyle(Palette.ink2)
        TextEditor(text: $text)
          .focused($focused)
          .scrollContentBackground(.hidden)
          .padding(10)
          .background(Palette.fill, in: .rect(cornerRadius: 12))
          .overlay(alignment: .topLeading) {
            if text.isEmpty {
              Text("It closed in 2025. It’s in Brooklyn, not Queens…")
                .foregroundStyle(Palette.ink3)
                .padding(.horizontal, 15)
                .padding(.vertical, 18)
                .allowsHitTesting(false)
            }
          }
        if let error { Text(error).font(.footnote).foregroundStyle(Palette.failed) }
      }
      .padding(20)
      .navigationTitle("Correct this page")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
        ToolbarItem(placement: .confirmationAction) {
          Button("Send") { Task { await submit() } }.disabled(trimmed.isEmpty || sending)
        }
      }
    }
    .presentationDetents([.medium, .large])
    .onAppear { focused = true }
  }

  private func submit() async {
    sending = true
    defer { sending = false }
    do {
      try await send(trimmed)
      Haptics.success()
      dismiss()
    } catch {
      Haptics.error()
      self.error = error.localizedDescription
    }
  }
}
