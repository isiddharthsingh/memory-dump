import SwiftUI

/// The Ask tab: the chat, with History in a drawer on its left. Swiping right
/// anywhere on the chat pulls the drawer open with the finger, as does the
/// History button; tapping or dragging the chat back closes it.
struct AskTab: View {
  @Environment(Router.self) private var router
  @State private var model = AskModel()
  @State private var drawer = HistoryDrawer()

  var body: some View {
    @Bindable var router = router
    DrawerSlide(drawer: drawer, canOpen: router.askPath.isEmpty) {
      NavigationStack(path: $router.askPath) {
        AskView(model: model, drawer: drawer).withRoutes()
      }
    } history: {
      ChatHistoryView(model: model, drawer: drawer)
    }
  }
}

/// Whether History is open. The drag in progress stays in DrawerSlide, which
/// tells the chat and the list whether the drawer shows through
/// `historyShown`, so they redraw when it opens or closes, not every frame.
@Observable
final class HistoryDrawer {
  var isOpen = false

  func setOpen(_ open: Bool) {
    withAnimation(.snappy(duration: 0.3)) { isOpen = open }
  }
}

extension EnvironmentValues {
  /// The History drawer shows, even partly: the tab bar and the keyboard make way.
  @Entry var historyShown = false
}

/// The chat and History side by side, moved by the finger.
private struct DrawerSlide<Chat: View, History: View>: View {
  let drawer: HistoryDrawer
  /// Off while a pushed screen is on top, so a right swipe there goes back.
  let canOpen: Bool
  @ViewBuilder let chat: Chat
  @ViewBuilder let history: History

  /// How far a swipe has pulled the closed drawer out.
  @State private var opening: CGFloat = 0
  /// How far a drag on the chat has pushed the open drawer back.
  @State private var closing: CGFloat = 0
  @State private var screenWidth: CGFloat = 0

  var body: some View {
    let width = min(screenWidth * 0.84, 360)
    let shown = min(max((drawer.isOpen ? width : 0) + opening + closing, 0), width)
    let progress = width > 0 ? shown / width : 0
    ZStack(alignment: .leading) {
      chat
        .gesture(
          DrawerPan(direction: 1, isEnabled: canOpen && !drawer.isOpen) { opening = max(0, $0) } ended: { projected in
            withAnimation(.snappy(duration: 0.3)) {
              opening = 0
              drawer.setOpen(projected > width / 2)
            }
          }
        )
        .overlay {
          Palette.scrim.opacity(progress)
            .ignoresSafeArea()
            .contentShape(.rect)
            .allowsHitTesting(drawer.isOpen)
            .onTapGesture { drawer.setOpen(false) }
            // The same UIKit pan, leftward: a SwiftUI drag here loses the touch
            // to the chat's scroll view underneath.
            .gesture(
              DrawerPan(direction: -1, isEnabled: drawer.isOpen) { closing = min(0, $0) } ended: { projected in
                withAnimation(.snappy(duration: 0.3)) {
                  closing = 0
                  drawer.setOpen(projected > -width / 2)
                }
              }
            )
        }
        .accessibilityHidden(drawer.isOpen)
        .offset(x: shown)

      history
        .frame(width: width)
        .offset(x: shown - width)
        .accessibilityHidden(!drawer.isOpen)
        .accessibilityAction(.escape) { drawer.setOpen(false) }
    }
    .environment(\.historyShown, drawer.isOpen || opening > 0)
    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { screenWidth = $0 }
  }
}

/// A mostly horizontal pan one way. UIKit's pan rather than DragGesture, so
/// the chat still scrolls vertically and a row of source cards keeps its own
/// horizontal drag. Reports how far it moved, then where a flick would have
/// carried it; a cancelled pan reports no movement.
private struct DrawerPan: UIGestureRecognizerRepresentable {
  /// 1 for rightward, which opens the drawer; -1 for leftward, which closes it.
  let direction: CGFloat
  let isEnabled: Bool
  let changed: (CGFloat) -> Void
  let ended: (CGFloat) -> Void

  func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
    let pan = UIPanGestureRecognizer()
    pan.delegate = context.coordinator
    return pan
  }

  func updateUIGestureRecognizer(_ pan: UIPanGestureRecognizer, context: Context) {
    pan.isEnabled = isEnabled
  }

  func handleUIGestureRecognizerAction(_ pan: UIPanGestureRecognizer, context: Context) {
    let x = pan.translation(in: pan.view).x
    switch pan.state {
    case .changed: changed(x)
    case .ended: ended(x + pan.velocity(in: pan.view).x * 0.2)
    case .cancelled, .failed: ended(0)
    default: break
    }
  }

  func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
    Coordinator(direction: direction)
  }

  final class Coordinator: NSObject, UIGestureRecognizerDelegate {
    let direction: CGFloat

    init(direction: CGFloat) { self.direction = direction }

    func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
      guard let pan = recognizer as? UIPanGestureRecognizer else { return false }
      let velocity = pan.velocity(in: pan.view)
      return velocity.x * direction > abs(velocity.y)
    }

    /// Drags in the question field stay with it, for moving the cursor and
    /// dragging selection handles. Only while closed: open, the field sits
    /// under the dimmed chat.
    func gestureRecognizer(_ recognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
      guard direction > 0 else { return true }
      var view = touch.view
      while let v = view {
        if v is UITextView || v is UITextField { return false }
        view = v.superview
      }
      return true
    }

    /// A row that scrolls sideways gets the drag first.
    func gestureRecognizer(
      _ recognizer: UIGestureRecognizer, shouldRequireFailureOf other: UIGestureRecognizer
    ) -> Bool {
      guard let scroll = other.view as? UIScrollView, other === scroll.panGestureRecognizer else { return false }
      return scroll.contentSize.width > scroll.bounds.width
    }
  }
}

struct AskView: View {
  let model: AskModel
  let drawer: HistoryDrawer
  @Environment(Router.self) private var router
  @Environment(\.historyShown) private var historyShown
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
          drawer.setOpen(true)
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
    .toolbar(historyShown ? .hidden : .automatic, for: .tabBar)
    .onChange(of: historyShown) { if historyShown { focused = false } }
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
          .foregroundStyle(Palette.onInk)
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
    .background(Palette.raised, in: .rect(cornerRadius: 26))
    .overlay { RoundedRectangle(cornerRadius: 26).strokeBorder(Palette.raisedEdge) }
    .shadow(color: Palette.shadow(0.10), radius: 16, y: 6)
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
        .foregroundStyle(Palette.onInk)
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
              .font(.caption2.weight(.heavy)).foregroundStyle(Palette.onPastel)
              .padding(.horizontal, 7).padding(.vertical, 3)
              .background(.white, in: .capsule)
            Text(title).font(.display(19, .heavy)).foregroundStyle(Palette.onPastel).lineLimit(3)
              .minimumScaleFactor(0.8)
            Spacer(minLength: 0)
          }
          .padding(10)
          .background(swatch.fill)
        } else {
          VStack(alignment: .leading, spacing: 0) {
            (isVideo ? Palette.video : isPost ? Palette.post : Palette.sand)
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
      .shadow(color: Palette.shadow(0.10), radius: 8, y: 4)
      .overlay(alignment: .topLeading) {
        Text("\(source.n)")
          .font(.caption.weight(.heavy)).foregroundStyle(Palette.onInk)
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
