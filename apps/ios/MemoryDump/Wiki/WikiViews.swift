import SwiftUI

struct WikiListView: View {
  @Environment(Router.self) private var router
  @State private var pages: [TopicPageSummary]?
  @State private var error: Error?

  var body: some View {
    content
      .navigationTitle("Wiki")
      .navigationSubtitle(pages.map { $0.isEmpty ? "" : "\(Format.plural($0.count, "topic")), written from your saves" } ?? "")
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
        List(pages) { page in
          Button { router.push(.wiki(page.slug)) } label: {
            LinkRow(
              title: Format.topicTitle(page.title),
              subtitle: "\(Format.plural(page.sourceItemIds?.count ?? 0, "source")) · updated \(Format.relativeTime(page.updatedAt))"
            ) { WikiTile() }
          }
          .buttonStyle(.plain)
          .listRowInsets(EdgeInsets(top: 10, leading: 20, bottom: 10, trailing: 20))
        }
        .listStyle(.plain)
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

  private func load() async {
    do {
      pages = try await APIClient.shared.wiki()
      error = nil
    } catch {
      if pages == nil { self.error = error }
    }
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
    do {
      async let pageResult = APIClient.shared.wikiPage(slug)
      // The list gives wikilinks and linked topics their real titles.
      async let list = try? APIClient.shared.wiki()
      let page = try await pageResult
      titles = Dictionary((await list ?? []).map { ($0.slug, $0.title) }, uniquingKeysWith: { a, _ in a })
      self.page = page
      order = LinkRewriter.citationOrder(page.bodyMd, sourceIds: page.sourceItemIds ?? [])
      blocks = MarkdownBlocks.parse(
        LinkRewriter.wiki(page.bodyMd, title: title, order: order, titles: titles))
      error = nil
      await loadSources()
    } catch {
      if page == nil { self.error = error }
    }
  }

  private func loadSources() async {
    for id in order where sources[id] == nil {
      guard let uuid = UUID(uuidString: id) else { continue }
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
          router.ask("What do I know about \(model.title)?")
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
        Label("Wiki", systemImage: "book.closed").font(.footnote.weight(.semibold)).foregroundStyle(Palette.ink2)
        Text(model.title)
          .font(.serif(34, .semibold, relativeTo: .largeTitle))
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
                Chip(label: Format.topicTitle(model.titles[slug] ?? slug)) { router.push(.wiki(slug)) }
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
