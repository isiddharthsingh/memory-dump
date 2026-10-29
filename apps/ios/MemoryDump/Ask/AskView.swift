import SwiftUI

struct AskView: View {
  @Environment(Router.self) private var router
  @State private var model = AskModel()
  @State private var voice = VoiceInput()
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
        .padding(.horizontal, 20)
        .padding(.top, 8)
      }
      .scrollDismissesKeyboard(.interactively)
      .onChange(of: model.turns.last?.answer) { proxy.scrollTo("end", anchor: .bottom) }
      .onChange(of: model.turns.count) { withAnimation { proxy.scrollTo("end", anchor: .bottom) } }
    }
    .safeAreaInset(edge: .bottom) { composer }
    .navigationTitle("Ask")
    .toolbar {
      if !model.turns.isEmpty {
        ToolbarItem(placement: .topBarTrailing) {
          Button("New question", systemImage: "square.and.pencil") { model.startOver() }
        }
      }
    }
    .task { await model.loadSuggestions() }
    .onAppear {
      voice.onText = { model.input = $0 }
      takeDraft()
    }
    .onChange(of: router.askDraft) { takeDraft() }
    .onDisappear { voice.stop() }
  }

  /// "Ask about this" on the reader or a topic opens this tab with a drafted question.
  private func takeDraft() {
    guard let draft = router.askDraft else { return }
    model.input = draft
    router.askDraft = nil
    focused = true
  }

  private var intro: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Ask your library").font(.serif(28, .semibold, relativeTo: .title)).foregroundStyle(Palette.ink)
      Text("Answers come only from what you’ve saved, with numbered sources you can open.")
        .font(.body).foregroundStyle(Palette.ink2)
      FlowLayout(spacing: 8) {
        ForEach(model.suggestions, id: \.self) { s in
          Chip(label: s) { model.submit(s) }
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
        model.isStreaming ? model.stop() : model.submit(model.input)
      } label: {
        Image(systemName: model.isStreaming ? "stop.fill" : "arrow.up")
          .font(.system(size: model.isStreaming ? 13 : 16, weight: .bold))
          .foregroundStyle(.white)
          .frame(width: 34, height: 34)
          .background(model.isStreaming || model.canSend ? Palette.ink : Palette.hairlineStrong, in: .circle)
      }
      .disabled(!model.isStreaming && !model.canSend)
      .accessibilityLabel(model.isStreaming ? "Stop answering" : "Send question")
    }
    .padding(.leading, 8)
    .padding(.trailing, 6)
    .padding(.vertical, 4)
    .glassEffect(.regular, in: .rect(cornerRadius: 24))
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
    VStack(alignment: .leading, spacing: 20) {
      Text(turn.question)
        .font(.serif(26, .semibold, relativeTo: .title))
        .foregroundStyle(Palette.ink)
        .accessibilityAddTraits(.isHeader)

      if !numbered.sources.isEmpty {
        VStack(alignment: .leading, spacing: 8) {
          Text("\(Format.plural(numbered.sources.count, "source")) from your library")
            .font(.footnote.weight(.semibold)).foregroundStyle(Palette.ink2)
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
              ForEach(numbered.sources) { source in sourceCard(source) }
            }
          }
        }
      }

      if case .failed(let title, let detail) = turn.status {
        ErrorNotice(title: title, detail: detail, retry: retry)
      } else {
        VStack(alignment: .leading, spacing: 10) {
          Label("Answer", systemImage: "sparkles").font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent)
          if turn.answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && turn.isStreaming {
            HStack(spacing: 8) {
              ProgressView()
              Text("Searching your library…").font(.subheadline).foregroundStyle(Palette.ink3)
            }
          } else {
            MarkdownView(
              LinkRewriter.answer(turn.answer, order: numbered.order), bodyFont: .serif(17), lineSpacing: 6)
          }
        }
      }
    }
  }

  private func sourceCard(_ source: LinkRewriter.NumberedSource) -> some View {
    let c = source.citation
    let isWiki = c.kind == "topic_page"
    let where_ = isWiki ? "Wiki" : c.url.map(Format.domain) ?? "Saved item"
    let title = isWiki ? Format.topicTitle(c.title ?? c.slug ?? "") : c.title ?? where_
    return Button {
      router.push(isWiki ? .wiki(c.slug ?? "") : .item(c.id))
    } label: {
      VStack(alignment: .leading, spacing: 6) {
        HStack(spacing: 6) {
          Text("\(source.n)")
            .font(.caption.bold()).foregroundStyle(.white)
            .frame(width: 18, height: 18)
            .background(Palette.accent, in: .circle)
          Text(where_).font(.caption).foregroundStyle(Palette.ink3).lineLimit(1)
        }
        Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.ink).lineLimit(2)
          .multilineTextAlignment(.leading)
        Spacer(minLength: 0)
      }
      .padding(12)
      .frame(width: 164, height: 96, alignment: .topLeading)
      .background(Palette.surfaceMuted, in: .rect(cornerRadius: 14))
    }
    .buttonStyle(.plain)
    .accessibilityLabel("Source \(source.n): \(title), \(where_)")
  }
}
