import SwiftUI

struct AskView: View {
  @Environment(Router.self) private var router
  @State private var model = AskModel()
  @State private var voice = VoiceInput()
  @State private var showHistory = false
  @FocusState private var focused: Bool

  var body: some View {
    @Bindable var model = model
    ScrollViewReader { proxy in
      ScrollView {
        VStack(alignment: .leading, spacing: 32) {
          if model.turns.isEmpty {
            intro
          } else {
            ForEach(model.turns) { turn in
              AskTurnView(turn: turn) { model.retry(turn) }.id(turn.id)
            }
          }
          Color.clear.frame(height: 1).id("end")
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
      }
      .scrollDismissesKeyboard(.interactively)
      .onChange(of: model.turns.last?.answer) { proxy.scrollTo("end", anchor: .bottom) }
      .onChange(of: model.turns.count) { withAnimation { proxy.scrollTo("end", anchor: .bottom) } }
    }
    .background(Palette.ground)
    .safeAreaInset(edge: .bottom) { composer }
    .navigationTitle("Ask")
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      ToolbarItem(placement: .topBarLeading) {
        Button("History", systemImage: "clock.arrow.circlepath") {
          Haptics.tap()
          showHistory = true
        }
      }
      ToolbarItem(placement: .topBarTrailing) {
        Button("New chat", systemImage: "square.and.pencil") {
          Haptics.press()
          withAnimation { model.newChat() }
        }
        .disabled(model.turns.isEmpty)
      }
    }
    .sheet(isPresented: $showHistory) { ChatHistoryView(model: model) }
    .task { await model.loadSuggestions() }
    .onAppear {
      voice.onText = { model.input = $0 }
      takeDraft()
    }
    .onChange(of: router.askDraft) { takeDraft() }
    .onDisappear { voice.stop() }
  }

  /// "Ask about this" on the reader or a topic opens this tab with a drafted
  /// question, in a new chat so it doesn't land in an unrelated one.
  private func takeDraft() {
    guard let draft = router.askDraft else { return }
    if !model.turns.isEmpty { model.newChat() }
    model.input = draft
    router.askDraft = nil
    focused = true
  }

  private var intro: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Ask your library").font(.display(32, .heavy, relativeTo: .title)).foregroundStyle(Palette.ink)
      Text("Answers come only from what you’ve saved, with numbered sources you can open.")
        .font(.body).foregroundStyle(Palette.ink2)
      FlowLayout(spacing: 8) {
        ForEach(model.suggestions, id: \.self) { s in
          Chip(label: s, symbol: "sparkle") { model.submit(s) }
        }
      }
      .padding(.top, 8)
    }
    .padding(.top, 24)
  }

  private var composer: some View {
    @Bindable var model = model
    return HStack(alignment: .bottom, spacing: 8) {
      Button {
        Task { await voice.toggle() }
      } label: {
        Image(systemName: voice.isListening ? "stop.circle.fill" : "mic")
          .font(.title3)
          .foregroundStyle(voice.isListening ? Palette.failed : Palette.ink2)
          .frame(width: 36, height: 36)
      }
      .accessibilityLabel(voice.isListening ? "Stop listening" : "Ask with your voice")

      TextField(
        model.turns.isEmpty ? "Ask anything you’ve saved" : "Ask another question", text: $model.input,
        axis: .vertical
      )
      .lineLimit(1...5)
      .focused($focused)
      .submitLabel(.send)
      .onSubmit { model.submit(model.input) }
      .padding(.vertical, 8)
      .accessibilityLabel("Question")

      Button {
        if model.isStreaming {
          Haptics.tap()
          model.stop()
        } else {
          model.submit(model.input)
        }
      } label: {
        Image(systemName: model.isStreaming ? "stop.fill" : "arrow.up")
          .font(.system(size: model.isStreaming ? 14 : 17, weight: .bold))
          .foregroundStyle(.white)
          .frame(width: 40, height: 40)
          .background(
            model.isStreaming ? Palette.ink : model.canSend ? Palette.accent : Palette.hairlineStrong, in: .circle)
      }
      .disabled(!model.isStreaming && !model.canSend)
      .accessibilityLabel(model.isStreaming ? "Stop answering" : "Send question")
    }
    .padding(.leading, 8)
    .padding(.trailing, 6)
    .padding(.vertical, 6)
    .background(Palette.card, in: .rect(cornerRadius: 26))
    .shadow(color: Palette.ink.opacity(0.10), radius: 16, y: 6)
    .padding(.horizontal, 12)
    .padding(.bottom, 8)
  }
}

private struct AskTurnView: View {
  let turn: AskModel.Turn
  let retry: () -> Void
  @Environment(Router.self) private var router

  var body: some View {
    let numbered = LinkRewriter.numberSources(answer: turn.answer, citations: turn.citations)
    VStack(alignment: .leading, spacing: 16) {
      Text(turn.question)
        .font(.body.weight(.medium))
        .foregroundStyle(.white)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(
          Palette.ink,
          in: UnevenRoundedRectangle(
            topLeadingRadius: 22, bottomLeadingRadius: 22, bottomTrailingRadius: 6, topTrailingRadius: 22))
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.leading, 48)
        .accessibilityAddTraits(.isHeader)

      if !numbered.sources.isEmpty {
        VStack(alignment: .leading, spacing: 0) {
          Text("\(Format.plural(numbered.sources.count, "source")) from your library")
            .font(.footnote.weight(.semibold)).foregroundStyle(Palette.ink2)
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
              ForEach(numbered.sources) { source in sourceCard(source) }
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
          }
          .padding(.horizontal, -16)
        }
      }

      if case .failed(let title, let detail) = turn.status {
        ErrorNotice(title: title, detail: detail, retry: retry)
      } else {
        VStack(alignment: .leading, spacing: 10) {
          Label("Answer", systemImage: "sparkles").font(.footnote.weight(.bold)).foregroundStyle(Palette.accent)
          if turn.answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && turn.isStreaming {
            HStack(spacing: 8) {
              ProgressView()
              Text("Searching your library…").font(.subheadline).foregroundStyle(Palette.ink3)
            }
          } else {
            MarkdownView(LinkRewriter.answer(turn.answer, order: numbered.order), bodyFont: .body, lineSpacing: 5)
          }
        }
        .padding(EdgeInsets(top: 16, leading: 18, bottom: 18, trailing: 18))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card, in: .rect(cornerRadius: 24))
      }
    }
  }

  private static let tilts: [Double] = [-4, 2, -2, 3, -1]

  /// A small card per source, tilted so the row reads as a fan. Wiki pages
  /// take their topic's color; videos and posts look like their library cards.
  private func sourceCard(_ source: LinkRewriter.NumberedSource) -> some View {
    let c = source.citation
    let isWiki = c.kind == "topic_page"
    let host = c.url.map(Format.domain) ?? ""
    let where_ = isWiki ? "Wiki" : host.isEmpty ? "Saved item" : host
    let title = isWiki ? Format.topicTitle(c.title ?? c.slug ?? "") : c.title ?? where_
    let kind = Format.source(ofURL: c.url)
    let isVideo = kind == "youtube"
    let isPost = kind == "twitter"
    let swatch = Swatch.topic(c.slug ?? "")
    return Button {
      router.push(isWiki ? .wiki(c.slug ?? "") : .item(c.id))
    } label: {
      Group {
        if isWiki {
          VStack(alignment: .leading, spacing: 8) {
            Text("Wiki")
              .font(.caption2.weight(.heavy)).foregroundStyle(Palette.ink)
              .padding(.horizontal, 7).padding(.vertical, 3)
              .background(.white, in: .capsule)
            Text(title).font(.display(19, .heavy)).foregroundStyle(Palette.ink).lineLimit(3)
              .minimumScaleFactor(0.8)
            Spacer(minLength: 0)
          }
          .padding(10)
          .background(swatch.fill)
        } else {
          VStack(alignment: .leading, spacing: 0) {
            (isVideo ? Palette.video : isPost ? Palette.ink : Palette.sand)
              .frame(height: 40)
              .overlay {
                if isVideo || isPost {
                  Image(systemName: isVideo ? "play.fill" : "xmark").font(.caption.bold()).foregroundStyle(.white)
                }
              }
            Text(title).font(.caption.weight(.bold)).foregroundStyle(Palette.ink).lineLimit(3)
              .multilineTextAlignment(.leading)
              .padding(.horizontal, 10).padding(.top, 8)
            Spacer(minLength: 0)
            Text(where_).font(.caption2).foregroundStyle(Palette.ink2).lineLimit(1)
              .padding(.horizontal, 10).padding(.bottom, 8)
          }
          .background(Palette.card)
        }
      }
      .frame(width: 124, height: 124, alignment: .topLeading)
      .clipShape(.rect(cornerRadius: 18))
      .shadow(color: Palette.ink.opacity(0.10), radius: 8, y: 4)
      .overlay(alignment: .topLeading) {
        Text("\(source.n)")
          .font(.caption.weight(.heavy)).foregroundStyle(.white)
          .frame(width: 22, height: 22)
          .background(Palette.ink, in: .circle)
          .offset(x: -6, y: -6)
      }
      .rotationEffect(.degrees(Self.tilts[(source.n - 1) % Self.tilts.count]))
    }
    .buttonStyle(PressableCardStyle())
    .accessibilityLabel("Source \(source.n): \(title), \(where_)")
  }
}
