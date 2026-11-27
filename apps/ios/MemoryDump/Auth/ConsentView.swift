import SwiftUI

/// Asks the account to agree to its content going to the AI services before
/// the app shows anything else (App Review guideline 5.1.2(i)). RootView shows
/// it while the session's user has no `aiConsentAt`; agreeing keeps the user
/// the server answers with, which lets the app through. Settings › Privacy
/// withdraws it, which brings this back.
struct ConsentView: View {
  @Environment(AppModel.self) private var app
  @State private var agreeing = false
  @State private var signingOut = false
  @State private var error: String?

  private struct Receiver: Hashable {
    let name: String
    let does: String
  }

  private static let sent = [
    "The links, notes, photos and PDFs you save, and the text of the pages they link to",
    "What you type or say in Search and Ask",
  ]

  private static let receivers = [
    Receiver(name: "Anthropic (Claude)", does: "reads your saves, writes your wiki and answers your questions"),
    Receiver(name: "Voyage AI", does: "indexes your saves so Search and Ask can find them"),
    Receiver(name: "FxTwitter", does: "reads posts on X for you (it receives the post’s link)"),
    Receiver(
      name: "Jina AI", does: "fetches a page’s text when a site blocks a direct request (it receives the link)"),
    Receiver(
      name: "If you add your own API key in Settings",
      does: "that provider (OpenAI, Google, OpenRouter or Amazon Bedrock) instead of Anthropic"),
  ]

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 22) {
        VStack(alignment: .leading, spacing: 10) {
          Text("Before you start")
            .font(.display(32, .heavy, relativeTo: .largeTitle))
            .foregroundStyle(Palette.ink)
            .accessibilityAddTraits(.isHeader)
          Text(
            "Memory Dump uses AI services to read, summarize and organize what you save, and to answer your questions. To do that, it sends them your content."
          )
          .font(.body)
          .foregroundStyle(Palette.ink2)
        }

        section("What’s sent") {
          ForEach(Self.sent, id: \.self) { line in
            bullet(Text(line))
          }
        }

        section("Who receives it") {
          ForEach(Self.receivers, id: \.self) { receiver in
            bullet(Text("\(Text(receiver.name).fontWeight(.semibold)): \(receiver.does)"))
          }
        }

        Text(
          "Each service handles your content under its own privacy terms. Memory Dump doesn’t sell your data or use it for advertising. You can withdraw this anytime in Settings, but Memory Dump can’t save or answer anything new without it."
        )
        .font(.subheadline)
        .foregroundStyle(Palette.ink2)

        Link("Privacy Policy", destination: PublicPage.privacy)
          .font(.subheadline.weight(.semibold))
          .foregroundStyle(Palette.accent)
      }
      .padding(24)
      .padding(.top, 24)
      .frame(maxWidth: .infinity, alignment: .leading)
    }
    .background(Palette.bg)
    // The choice stays in reach however long the text runs.
    .safeAreaInset(edge: .bottom) { actions }
  }

  private var actions: some View {
    VStack(spacing: 8) {
      if let error {
        ErrorNotice(title: "Couldn’t save your choice", detail: error)
      }
      Button {
        Task { await agree() }
      } label: {
        Group {
          if agreeing { ProgressView().tint(Palette.onInk) } else { Text("Agree and Continue") }
        }
        .font(.headline)
        .foregroundStyle(Palette.onInk)
        .frame(maxWidth: .infinity, minHeight: 50)
      }
      .buttonStyle(.borderedProminent)
      .tint(Palette.ink)
      .disabled(agreeing || signingOut)

      Button {
        Task { await signOut() }
      } label: {
        Group {
          if signingOut { ProgressView() } else { Text("Sign Out") }
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(Palette.ink2)
        .frame(maxWidth: .infinity, minHeight: 44)
      }
      .disabled(agreeing || signingOut)
    }
    .padding(.horizontal, 24)
    .padding(.top, 12)
    .padding(.bottom, 4)
    .background(Palette.bg)
  }

  private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(title)
        .font(.headline)
        .foregroundStyle(Palette.ink)
        .accessibilityAddTraits(.isHeader)
      content()
    }
  }

  private func bullet(_ text: Text) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
      Text(verbatim: "•").foregroundStyle(Palette.ink3).accessibilityHidden(true)
      text
        .foregroundStyle(Palette.ink2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    .font(.subheadline)
  }

  private func agree() async {
    guard !agreeing else { return }
    agreeing = true
    error = nil
    defer { agreeing = false }
    do {
      // The server's answer carries the consent; keeping it opens the app.
      try await app.setAIConsent(true)
      Haptics.success()
    } catch is URLError {
      error = "Can’t connect to Memory Dump. Check your connection and try again."
    } catch {
      self.error = error.localizedDescription
    }
    if error != nil { Haptics.error() }
  }

  private func signOut() async {
    signingOut = true
    await app.signOut()
    signingOut = false
  }
}
