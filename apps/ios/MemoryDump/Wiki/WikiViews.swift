import SwiftUI

struct WikiListView: View {
  @Environment(Router.self) private var router
  @State private var pages: [TopicPageSummary]?
  @State private var error: Error?

  var body: some View {
    content
      .frame(maxWidth: .infinity, maxHeight: .infinity)
      .background(Palette.ground)
      .navigationTitle("Wiki")
      .navigationSubtitle(
        APIClient.shared.isOffline
          ? Format.offline
          : pages.map { $0.isEmpty ? "" : "\(Format.plural($0.count, "topic")), written from your saves" } ?? "")
      .task { await load() }
  }

  @ViewBuilder private var content: some View {
    if let pages {
      if pages.isEmpty {
        ContentUnavailableView {
          Label("Your wiki is empty", systemImage: "book.closed")
        } description: {
          Text("As you save links, topic pages are written and kept up to date here, each linking back to its sources.")
        }
      } else {
        ScrollView {
          VStack(alignment: .leading, spacing: 10) {
            if let latest = pages.max(by: { $0.updatedAt < $1.updatedAt }) {
              topicButton(latest) { RevisedRibbon(page: latest) }
            }
            let featured = Self.featured(pages)
            if let featured {
              topicButton(featured) { FeaturedTopicTile(page: featured, showsLabel: pages.count > 1) }
            }
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
              ForEach(pages.filter { $0.id != featured?.id }) { page in
                topicButton(page) { TopicTile(page: page) }
              }
            }
          }
          .padding(.horizontal, 16)
          .padding(.bottom, 24)
        }
        .refreshable { await load() }
      }
    } else if let error {
      ErrorNotice(title: "Couldn’t load your wiki", detail: error.localizedDescription) { Task { await load() } }
        .padding(20)
        .frame(maxHeight: .infinity, alignment: .top)
    } else {
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
    }
  }

  private func topicButton<Label: View>(_ page: TopicPageSummary, @ViewBuilder label: () -> Label) -> some View {
    Button { router.push(.wiki(page.slug)) } label: { label() }
      .buttonStyle(PressableCardStyle())
      .accessibilityLabel(
        "\(Format.topicTitle(page.title)), \(Format.plural(page.sourceItemIds?.count ?? 0, "source")), updated \(Format.relativeTime(page.updatedAt))")
  }

  /// The topic with the most sources, newest first on a tie.
  static func featured(_ pages: [TopicPageSummary]) -> TopicPageSummary? {
    pages.max { a, b in
      let (na, nb) = (a.sourceItemIds?.count ?? 0, b.sourceItemIds?.count ?? 0)
      return na == nb ? a.updatedAt < b.updatedAt : na < nb
    }
  }

  private func load() async {
    if pages == nil { pages = try? await APIClient.saved.wiki() }
    do {
      pages = try await APIClient.shared.wiki()
      error = nil
    } catch {
      if pages == nil { self.error = error }
    }
  }
}

/// The most recently revised page: "Updated 2h ago" over its title.
private struct RevisedRibbon: View {
  let page: TopicPageSummary

  var body: some View {
    HStack(spacing: 12) {
      Circle().fill(Palette.fresh).frame(width: 9, height: 9)
      VStack(alignment: .leading, spacing: 1) {
        Text("Updated \(Format.relativeTime(page.updatedAt))")
          .font(.caption.weight(.semibold))
          .foregroundStyle(Palette.ink2)
        Text(Format.topicTitle(page.title))
          .font(.subheadline.weight(.semibold))
          .foregroundStyle(Palette.ink)
          .lineLimit(1)
      }
      Spacer(minLength: 0)
      Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Palette.hairlineStrong)
    }
    .padding(.horizontal, 14)
    .padding(.vertical, 9)
    .frame(minHeight: 52)
    .background(Palette.card, in: .rect(cornerRadius: 18))
  }
}

/// The biggest topic, full width, with a fan of cards standing in for its sources.
private struct FeaturedTopicTile: View {
  let page: TopicPageSummary
  let showsLabel: Bool
  /// Grows with the title's text size, so larger type isn't clipped.
  @ScaledMetric(relativeTo: .largeTitle) private var height: CGFloat = 188

  var body: some View {
    let swatch = Swatch.topic(page.slug)
    ZStack(alignment: .topTrailing) {
      VStack(alignment: .leading, spacing: 0) {
        if showsLabel {
          Text("Your biggest topic").font(.caption.weight(.bold)).foregroundStyle(swatch.ink)
        }
        Text(Format.topicTitle(page.title))
          .font(.display(34, .heavy, relativeTo: .largeTitle))
          .foregroundStyle(Palette.onPastel)
          .lineLimit(3)
          .minimumScaleFactor(0.6)
          .multilineTextAlignment(.leading)
          .padding(.top, 4)
        Spacer(minLength: 12)
        Text("\(Format.plural(page.sourceItemIds?.count ?? 0, "source")) · updated \(Format.relativeTime(page.updatedAt))")
          .font(.caption.weight(.bold))
          .foregroundStyle(swatch.ink)
      }
      .frame(maxWidth: 190, maxHeight: .infinity, alignment: .topLeading)
      .frame(maxWidth: .infinity, alignment: .leading)
      SourceFan(behind: swatch).padding(.top, 28).padding(.trailing, -8)
    }
    .padding(18)
    .frame(height: height)
    .background(swatch.fill, in: .rect(cornerRadius: 26))
    .clipShape(.rect(cornerRadius: 26))
  }
}

private struct SourceFan: View {
  let behind: Swatch

  var body: some View {
    let note = behind == .mint ? Swatch.lilac : .mint
    ZStack(alignment: .topLeading) {
      card(band: Palette.sand).rotationEffect(.degrees(-12)).offset(y: 12)
      card(band: Palette.video, play: true).rotationEffect(.degrees(3)).offset(x: 30)
      VStack(alignment: .leading, spacing: 5) {
        Capsule().fill(note.ink.opacity(0.3)).frame(height: 6)
        Capsule().fill(note.ink.opacity(0.3)).frame(height: 6)
        Capsule().fill(note.ink.opacity(0.3)).frame(width: 34, height: 6)
        Spacer(minLength: 0)
      }
      .padding(10)
      .frame(width: 72, height: 92)
      .background(note.fill, in: .rect(cornerRadius: 14))
      .shadow(color: Palette.shadow(0.14), radius: 8, y: 6)
      .rotationEffect(.degrees(14))
      .offset(x: 66, y: 26)
    }
    .frame(width: 140, height: 130, alignment: .topLeading)
    .accessibilityHidden(true)
  }

  private func card(band: Color, play: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 5) {
      band.frame(height: 48).overlay {
        if play { Image(systemName: "play.fill").font(.caption).foregroundStyle(.white) }
      }
      Capsule().fill(Palette.hairline).frame(height: 6).padding(.horizontal, 8)
      Capsule().fill(Palette.hairline).frame(width: 36, height: 6).padding(.leading, 8)
      Spacer(minLength: 0)
    }
    .frame(width: 72, height: 92)
    .background(Palette.card)
    .clipShape(.rect(cornerRadius: 14))
    .shadow(color: Palette.shadow(0.14), radius: 8, y: 6)
  }
}

struct TopicTile: View {
  let page: TopicPageSummary

  private static let dots = [Palette.video, Palette.post, Palette.card]

  var body: some View {
    let swatch = Swatch.topic(page.slug)
    let count = page.sourceItemIds?.count ?? 0
    VStack(alignment: .leading, spacing: 4) {
      Text(Format.topicTitle(page.title))
        .font(.display(22, .heavy))
        .foregroundStyle(Palette.onPastel)
        .lineLimit(3)
        .minimumScaleFactor(0.8)
        .multilineTextAlignment(.leading)
      Text(Format.plural(count, "source")).font(.caption.weight(.semibold)).foregroundStyle(swatch.ink)
      Spacer(minLength: 10)
      HStack(spacing: 8) {
        HStack(spacing: -8) {
          ForEach(0..<min(count, Self.dots.count), id: \.self) { i in
            Circle().fill(Self.dots[i]).frame(width: 26, height: 26)
              .overlay { Circle().strokeBorder(swatch.fill, lineWidth: 2) }
          }
        }
        Text(Format.relativeTime(page.updatedAt)).font(.caption).foregroundStyle(swatch.ink).lineLimit(1)
      }
      .accessibilityHidden(true)
    }
    .padding(14)
    .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
    .background(swatch.fill, in: .rect(cornerRadius: 24))
  }
}

@Observable
final class WikiPageModel {
  let slug: String
  private(set) var page: TopicPageDetail?
  private(set) var error: Error?
  private(set) var blocks: [MarkdownBlock] = []
  private(set) var order: [String] = []
  private(set) var sources: [String: ItemDetail] = [:]
  private(set) var titles: [String: String] = [:]

  init(slug: String) { self.slug = slug }

  var title: String { page.map { Format.topicTitle($0.title) } ?? "" }

  func load() async {
    if page == nil, let saved = try? await APIClient.saved.wikiPage(slug) {
      show(saved, list: (try? await APIClient.saved.wiki()) ?? [])
    }
    do {
      async let pageResult = APIClient.shared.wikiPage(slug)
      // The list gives wikilinks and linked topics their real titles.
      async let list = try? APIClient.shared.wiki()
      let page = try await pageResult
      show(page, list: await list ?? [])
      error = nil
    } catch {
      if page == nil { self.error = error }
    }
    await loadSources()
  }

  private func show(_ page: TopicPageDetail, list: [TopicPageSummary]) {
    titles = Dictionary(list.map { ($0.slug, $0.title) }, uniquingKeysWith: { a, _ in a })
    self.page = page
    order = LinkRewriter.citationOrder(page.bodyMd, sourceIds: page.sourceItemIds ?? [])
    blocks = MarkdownBlocks.parse(
      LinkRewriter.wiki(page.bodyMd, title: title, order: order, titles: titles))
  }

  /// Saved copies first, so the cards fill in at once, then the server's.
  private func loadSources() async {
    let missing = order.filter { sources[$0] == nil }.compactMap { id in UUID(uuidString: id).map { (id, $0) } }
    for (id, uuid) in missing {
      if let item = try? await APIClient.saved.item(uuid) { sources[id] = item }
    }
    for (id, uuid) in missing {
      if let item = try? await APIClient.shared.item(uuid) { sources[id] = item }
    }
  }
}

struct WikiPageView: View {
  @Environment(Router.self) private var router
  @State private var model: WikiPageModel

  init(slug: String) {
    _model = State(initialValue: WikiPageModel(slug: slug))
  }

  var body: some View {
    Group {
      if let page = model.page {
        content(page)
      } else if let error = model.error {
        ErrorNotice(title: "Couldn’t open this topic", detail: error.localizedDescription) {
          Task { await model.load() }
        }
        .padding(20)
      } else {
        ProgressView()
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(Palette.bg)
    .navigationBarTitleDisplayMode(.inline)
    .toolbar(.hidden, for: .tabBar)
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button("Ask about this topic", systemImage: "sparkles") {
          router.ask("What do I know about \(model.title)?", scope: .topic(slug: model.slug, title: model.title))
        }
        .tint(Palette.accent)
        .disabled(model.page == nil)
      }
    }
    .task { await model.load() }
  }

  private func content(_ page: TopicPageDetail) -> some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 0) {
        Label("Wiki", systemImage: "book.closed")
          .font(.caption.weight(.bold))
          .foregroundStyle(Swatch.topic(model.slug).ink)
          .padding(.horizontal, 10)
          .padding(.vertical, 5)
          .background(Swatch.topic(model.slug).fill, in: .capsule)
        Text(model.title)
          .font(.display(34, .heavy, relativeTo: .largeTitle))
          .foregroundStyle(Palette.ink)
          .padding(.top, 8)
          .accessibilityAddTraits(.isHeader)
        Text("Written from \(Format.plural(page.sourceItemIds?.count ?? 0, "source")) · updated \(Format.relativeTime(page.updatedAt))")
          .font(.subheadline).foregroundStyle(Palette.ink2).padding(.top, 8)
        Divider().padding(.vertical, 20)
        MarkdownView(blocks: model.blocks)

        if !model.order.isEmpty {
          section("Sources") {
            ForEach(Array(model.order.enumerated()), id: \.element) { i, id in
              let item = model.sources[id]
              Button {
                if let uuid = UUID(uuidString: id) { router.push(.item(uuid)) }
              } label: {
                LinkRow(
                  title: item.map { $0.title ?? Format.domain($0.url) } ?? "Loading…",
                  subtitle: item.map { Format.sourceMeta($0.url, $0.source) }
                ) {
                  HStack(spacing: 8) {
                    Text("\(i + 1)").font(.footnote.bold()).foregroundStyle(Palette.accent).frame(width: 16)
                    SourceThumb(source: item?.source ?? "web", url: item?.url, thumbnailURL: item?.thumbnailUrl, size: 40)
                  }
                }
              }
              .buttonStyle(.plain)
            }
          }
        }

        if let backlinks = page.backlinks, !backlinks.isEmpty {
          section("Linked topics") {
            FlowLayout(spacing: 8) {
              ForEach(backlinks, id: \.self) { slug in
                Chip(label: Format.topicTitle(model.titles[slug] ?? slug), dot: Swatch.topic(slug).fill) {
                  router.push(.wiki(slug))
                }
              }
            }
          }
        }
      }
      .padding(.horizontal, 24)
      .padding(.top, 8)
      .padding(.bottom, 32)
    }
  }

  private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      Divider().padding(.bottom, 8)
      Eyebrow(title)
      content()
    }
    .padding(.top, 24)
  }
}
