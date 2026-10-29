import SwiftUI

@Observable
final class ItemReaderModel {
  let id: UUID
  private(set) var item: ItemDetail?
  private(set) var error: Error?
  private(set) var related: RelatedResponse?
  private(set) var reader = ReaderContent(rawText: nil)
  private(set) var fullBlocks: [MarkdownBlock] = []
  /// Set when the body is long: the first part, cut at a paragraph or sentence.
  private(set) var previewBlocks: [MarkdownBlock]?
  private(set) var readMinutes = 0
  var retrying = false

  private static let previewMaxChars = 1200
  private static let collapseThreshold = 1600

  init(id: UUID) { self.id = id }

  func load() async {
    do {
      let item = try await APIClient.shared.item(id)
      apply(item)
      error = nil
      if item.status == "ready", related == nil { related = try? await APIClient.shared.related(id) }
    } catch {
      if item == nil { self.error = error }
    }
  }

  /// Polls every 2 seconds while the item is pending or processing.
  func pollWhileBusy() async {
    while !Task.isCancelled {
      try? await Task.sleep(for: .seconds(2))
      if item?.isBusy == true { await load() }
    }
  }

  func retry() async {
    retrying = true
    defer { retrying = false }
    if let item = try? await APIClient.shared.retryItem(id) { apply(item) }
  }

  func delete() async -> Bool {
    (try? await APIClient.shared.deleteItem(id)) != nil
  }

  private func apply(_ item: ItemDetail) {
    let rawChanged = item.rawText != self.item?.rawText || self.item?.title != item.title
    self.item = item
    guard rawChanged else { return }
    reader = ReaderContent(rawText: item.rawText)
    // Extracted bodies often open with the page title, which is already the heading.
    var body = reader.body
    if let b = body, let t = item.title?.trimmingCharacters(in: .whitespaces), !t.isEmpty, b.hasPrefix(t) {
      body = String(b.dropFirst(t.count)).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    // Extractors put one newline between paragraphs; markdown would join them.
    body = body.map { $0.split(separator: "\n").joined(separator: "\n\n") }
    fullBlocks = body.map(MarkdownBlocks.parse) ?? []
    readMinutes = body.map { max(1, ($0.split(whereSeparator: \.isWhitespace).count + 115) / 230) } ?? 0
    previewBlocks = body.flatMap(Self.preview).map(MarkdownBlocks.parse)
  }

  /// Cuts a long article at a paragraph or sentence, so it never ends mid-word.
  static func preview(of body: String) -> String? {
    guard body.count > collapseThreshold else { return nil }
    let slice = String(body.prefix(previewMaxChars))
    if let r = slice.range(of: "\n\n", options: .backwards), slice.distance(from: slice.startIndex, to: r.lowerBound) > 400 {
      return String(slice[..<r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    if let r = slice.range(of: ". ", options: .backwards), slice.distance(from: slice.startIndex, to: r.lowerBound) > 400 {
      return String(slice[...r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return slice.trimmingCharacters(in: .whitespacesAndNewlines) + "…"
  }
}


struct ItemReaderView: View {
  @Environment(Router.self) private var router
  @Environment(\.dismiss) private var dismiss
  @Environment(\.openURL) private var openURL
  @State private var model: ItemReaderModel
  @State private var expanded = false
  @State private var noteOpen = false
  @State private var confirmDelete = false
  /// Status bar plus navigation bar, which the hero runs underneath.
  @State private var topInset: CGFloat = 0
  /// The bars' fade would wash out the hero; it's needed only once text
  /// scrolls up under them.
  @State private var heroUnderBars = true

  private static let maxTags = 3

  init(id: UUID) {
    _model = State(initialValue: ItemReaderModel(id: id))
  }

  var body: some View {
    Group {
      if let item = model.item {
        reader(item)
      } else if let error = model.error {
        ErrorNotice(title: "Couldn’t open this item", detail: error.localizedDescription) {
          Task { await model.load() }
        }
        .padding(20)
      } else {
        ProgressView()
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .onGeometryChange(for: CGFloat.self) { $0.safeAreaInsets.top } action: { topInset = $0 }
    .background(Palette.ground)
    .navigationBarTitleDisplayMode(.inline)
    .toolbar(.hidden, for: .tabBar)
    .task { await model.load() }
    .task { await model.pollWhileBusy() }
  }

  private func reader(_ it: ItemDetail) -> some View {
    let title = it.title ?? Format.domain(it.url)
    let note = it.shareText ?? model.reader.note
    let style = ItemCardView.style(source: it.source, url: it.url, hasImage: it.thumbnailUrl != nil)
    let isPost = if case .post = style { true } else { false }
    // A tweet's text is its card, so it isn't repeated as the article.
    let tweetInHero = it.source == "twitter" && model.reader.bodySection == "Tweet"
    return ScrollView {
      VStack(alignment: .leading, spacing: 0) {
        ReaderHero(
          item: it, style: style, author: model.reader.author,
          postText: tweetInHero ? model.reader.body ?? title : title, topInset: topInset
        ) { open(it) }

        if !isPost {
          Text(title)
            .font(.readingTitle)
            .foregroundStyle(Palette.ink)
            .padding(.horizontal, 20)
            .padding(.top, 20)
            .accessibilityAddTraits(.isHeader)
          byline(it)
        }
        tags(it.tags ?? []).padding(.top, isPost ? 18 : 14)
        status(it).padding(.horizontal, 16).padding(.top, 20)
        if let note { noteCard(note) }
        if let summary = it.summary { gist(summary) }
        if it.status == "ready", !tweetInHero { article }
        connections
        details(it)
      }
      .padding(.bottom, 24)
    }
    // The hero runs to the top edge, so the status bar picks its text color
    // from the hero rather than from the ground above it.
    .ignoresSafeArea(edges: .top)
    .contentMargins(.top, topInset, for: .scrollIndicators)
    .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top < 150 } action: {
      heroUnderBars = $1
    }
    .scrollEdgeEffectHidden(heroUnderBars, for: .top)
    .safeAreaBar(edge: .bottom) { actionBar(it, title: title) }
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Menu("More actions", systemImage: "ellipsis") {
          Button("Add note", systemImage: "square.and.pencil") { noteOpen = true }
          ShareLink(item: it.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
          Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete = true }
        }
        // The app's ink tint vanishes on the dark post card while it is under the bar.
        .tint(isPost && heroUnderBars ? .white : nil)
      }
    }
    .confirmationDialog("Delete this item?", isPresented: $confirmDelete, titleVisibility: .visible) {
      Button("Delete", role: .destructive) {
        Task { if await model.delete() { Haptics.success(); dismiss() } else { Haptics.error() } }
      }
    }
    .sheet(isPresented: $noteOpen) {
      NoteEditor(itemId: it.id, title: title) { await model.load() }
    }
  }

  private func open(_ it: ItemDetail) {
    if let url = URL(string: it.url) { openURL(url) }
  }

  /// "By Dana Whitlock · Saved 3 days ago"; a video names its channel.
  private func byline(_ it: ItemDetail) -> some View {
    let saved = "Saved \(Format.relativeTime(it.createdAt))"
    let name = (model.reader.author ?? it.people?.first).map {
      Text($0).fontWeight(.semibold).foregroundStyle(Palette.ink)
    }
    return Group {
      if let name, it.source == "youtube" {
        Text("\(name) · \(saved)")
      } else if let name {
        Text("By \(name) · \(saved)")
      } else {
        Text(saved)
      }
    }
    .font(.subheadline)
    .foregroundStyle(Palette.ink2)
    .padding(.horizontal, 20)
    .padding(.top, 10)
  }

  /// Tags in their topic colors, like the Wiki tiles.
  @ViewBuilder private func tags(_ tags: [String]) -> some View {
    if !tags.isEmpty {
      FlowLayout(spacing: 8) {
        ForEach(tags.prefix(Self.maxTags), id: \.self) {
          Pill(label: Format.humanizeTag($0), fill: Swatch.topic($0).fill)
        }
        if tags.count > Self.maxTags { Pill(label: "+\(tags.count - Self.maxTags)") }
      }
      .padding(.horizontal, 20)
    }
  }

  @ViewBuilder private func status(_ it: ItemDetail) -> some View {
    if it.status == "failed" {
      if Format.isUploadedFile(it.url) {
        ErrorNotice(
          title: Format.friendlyIngestError(it.error, url: it.url),
          detail: "The file isn’t kept after a failed save. Share it to Memory Dump again to try once more.")
      } else {
        ErrorNotice(
          title: Format.friendlyIngestError(it.error, url: it.url),
          detail: "We couldn’t fetch \(Format.domain(it.url)). Check the link, or try again."
        ) { Task { await model.retry() } }
      }
    } else if it.status != "ready" {
      HStack(spacing: 10) {
        StatusBadge(status: it.status)
        Text("Summarizing and filing this link. It’ll be ready in a moment.")
          .font(.subheadline).foregroundStyle(Palette.ink2)
      }
      .padding(.horizontal, 4)
    }
  }

  /// The note as a mint sticky, tilted like a note pinned to a board.
  private func noteCard(_ note: String) -> some View {
    Button {
      noteOpen = true
    } label: {
      VStack(alignment: .leading, spacing: 8) {
        HStack {
          Eyebrow("Your note", color: Swatch.mint.ink)
          Spacer()
          Image(systemName: "square.and.pencil").font(.footnote.weight(.semibold)).foregroundStyle(Swatch.mint.ink)
        }
        Text(note)
          .font(.display(19, .semibold, relativeTo: .body))
          .foregroundStyle(Palette.ink)
          .multilineTextAlignment(.leading)
          .frame(maxWidth: .infinity, alignment: .leading)
      }
      .padding(EdgeInsets(top: 16, leading: 18, bottom: 18, trailing: 18))
      .background(Swatch.mint.fill, in: .rect(cornerRadius: 22))
      .shadow(color: Palette.ink.opacity(0.08), radius: 8, y: 6)
      .rotationEffect(.degrees(-1.5))
    }
    .buttonStyle(PressableCardStyle())
    .accessibilityHint("Edit your note")
    .padding(.horizontal, 22)
    .padding(.top, 24)
  }

  private func gist(_ summary: String) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Label("The gist", systemImage: "sparkle").font(.footnote.weight(.bold)).foregroundStyle(Palette.accent)
      Text(summary).font(.body).foregroundStyle(Palette.inkReading).lineSpacing(4)
    }
    .padding(EdgeInsets(top: 18, leading: 20, bottom: 20, trailing: 20))
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.card, in: .rect(cornerRadius: 24))
    .padding(.horizontal, 16)
    .padding(.top, 22)
  }

  /// The extracted text on a white sheet, cut short when it is long.
  private var article: some View {
    let readable = !model.fullBlocks.isEmpty && !model.reader.isStub
    return VStack(alignment: .leading, spacing: 12) {
      Eyebrow(readable ? articleLabel : "The article")
      if readable {
        MarkdownView(blocks: model.previewBlocks != nil && !expanded ? model.previewBlocks! : model.fullBlocks)
        if model.previewBlocks != nil {
          Button {
            expanded.toggle()
          } label: {
            Text(expanded ? "Show less" : "Continue reading")
              .font(.subheadline.weight(.bold))
              .foregroundStyle(.white)
              .frame(maxWidth: .infinity, minHeight: 46)
              .background(Palette.ink, in: .capsule)
          }
          .buttonStyle(PressableCardStyle())
          .padding(.top, 4)
        }
      } else {
        Text("There’s no readable version of this page. It may be paywalled or behind a bot wall. Open the original to read it.")
          .font(.callout)
          .foregroundStyle(Palette.ink2)
      }
    }
    .padding(20)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.card, in: .rect(cornerRadius: 24))
    .padding(.horizontal, 16)
    .padding(.top, 16)
  }

  private var articleLabel: String {
    switch model.reader.bodySection {
    case "Transcript": "Transcript"
    case "Description", "Summary": "Description"
    default: model.item?.source == "reddit" ? "The post" : "The article · \(model.readMinutes) min read"
    }
  }

  @ViewBuilder private var connections: some View {
    if let related = model.related {
      if !related.inWiki.isEmpty {
        section("In your wiki") {
          LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            ForEach(related.inWiki) { tp in
              Button { router.push(.wiki(tp.slug)) } label: { TopicTile(page: tp) }
                .buttonStyle(PressableCardStyle())
            }
          }
          .padding(.horizontal, 16)
        }
      }
      if !related.related.isEmpty {
        section("More like this") {
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 10) {
              ForEach(related.related.prefix(5)) { r in
                let card = MiniCard(item: r)
                Button { router.push(.item(r.id)) } label: { card }
                  .buttonStyle(PressableCardStyle())
                  .accessibilityLabel("\(card.title), \(card.meta)")
              }
            }
            .padding(.horizontal, 16)
          }
        }
      }
    }
  }

  /// What the analyzer pulled out, as pills.
  @ViewBuilder private func details(_ it: ItemDetail) -> some View {
    let people = it.people ?? []
    let mentions = (it.handles ?? []) + (it.brandsProducts ?? [])
    let topics = (it.topics ?? []).map(Format.humanizeTag)
    if !(people.isEmpty && mentions.isEmpty && topics.isEmpty) {
      section("Pulled from this save") {
        VStack(alignment: .leading, spacing: 16) {
          ForEach([("People", people), ("Mentioned", mentions), ("Topics", topics)], id: \.0) { label, values in
            if !values.isEmpty {
              VStack(alignment: .leading, spacing: 8) {
                Eyebrow(label)
                FlowLayout(spacing: 6) {
                  ForEach(values, id: \.self) {
                    Pill(label: $0, fill: Palette.ground, initials: label == "People" ? Format.initials($0) : nil)
                  }
                }
              }
            }
          }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card, in: .rect(cornerRadius: 24))
        .padding(.horizontal, 16)
      }
    }
  }

  private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      Text(title)
        .font(.display(22, .heavy, relativeTo: .title2))
        .foregroundStyle(Palette.ink)
        .padding(.horizontal, 20)
        .accessibilityAddTraits(.isHeader)
      content()
    }
    .padding(.top, 30)
  }

  /// One floating bar: open the original (or watch it), then note, Ask and
  /// Share. Uploaded files can't be reopened, so Ask takes the wide slot.
  private func actionBar(_ it: ItemDetail, title: String) -> some View {
    let uploaded = Format.isUploadedFile(it.url)
    let ask = { router.ask("What are the key ideas in “\(title)”?") }
    return HStack(spacing: 4) {
      if uploaded {
        wide("Ask about this", symbol: "sparkle", fill: Palette.accent, action: ask)
      } else {
        wide(openLabel(it.source), symbol: it.source == "youtube" ? "play.fill" : "arrow.up.right", fill: Palette.ink) {
          open(it)
        }
      }
      Button("Add a note", systemImage: "square.and.pencil") { noteOpen = true }
        .buttonStyle(BarIconStyle())
      if !uploaded {
        Button("Ask about this item", systemImage: "sparkle", action: ask)
          .buttonStyle(BarIconStyle(tint: Palette.accent, fill: Palette.accentSoft))
      }
      ShareLink(item: it.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
        .buttonStyle(BarIconStyle())
    }
    .padding(6)
    .background(Palette.card, in: .capsule)
    .shadow(color: Palette.ink.opacity(0.14), radius: 15, y: 10)
    .padding(.horizontal, 16)
    .padding(.bottom, 8)
  }

  private func wide(_ label: String, symbol: String, fill: Color, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Label(label, systemImage: symbol)
        .font(.subheadline.weight(.bold))
        .lineLimit(1)
        .minimumScaleFactor(0.8)
        .foregroundStyle(.white)
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, minHeight: 52)
        .background(fill, in: .capsule)
    }
    .buttonStyle(PressableCardStyle())
  }

  private func openLabel(_ source: String) -> String {
    switch source {
    case "youtube": "Watch on YouTube"
    case "twitter": "Open on X"
    case "reddit": "Open on Reddit"
    case "instagram": "Open on Instagram"
    default: "Open original"
    }
  }
}

/// A round icon button in the reader's action bar.
private struct BarIconStyle: ButtonStyle {
  var tint = Palette.ink2
  var fill = Color.clear

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .labelStyle(.iconOnly)
      .font(.title3)
      .foregroundStyle(tint)
      .frame(width: 58, height: 52)
      .background(fill, in: .capsule)
      .contentShape(.capsule)
      .opacity(configuration.isPressed ? 0.6 : 1)
  }
}
