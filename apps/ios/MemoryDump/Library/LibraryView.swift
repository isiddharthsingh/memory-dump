import SwiftUI

struct LibraryView: View {
  @Environment(AppModel.self) private var app
  @Environment(Router.self) private var router
  @Environment(\.horizontalSizeClass) private var sizeClass
  @State private var model = LibraryModel()
  @State private var query = ""
  @State private var noteTarget: ItemSummary?
  @State private var showSettings = false
  @State private var showSaveLink = false

  private static let maxTagChips = 12

  var body: some View {
    content
      .frame(maxWidth: .infinity, maxHeight: .infinity)
      .background(Palette.ground)
      .navigationTitle("Library")
      .navigationSubtitle(model.subtitle ?? "")
      .searchable(text: $query, prompt: "Search your library")
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("Save a link", systemImage: "plus") { showSaveLink = true }
        }
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            showSettings = true
          } label: {
            Text(Format.initials(app.session?.user.displayName ?? app.session?.user.email ?? "").nilIfEmpty ?? "?")
              .font(.footnote.weight(.semibold))
              .foregroundStyle(Palette.onInk)
              .frame(width: 32, height: 32)
              .background(Palette.ink, in: .circle)
          }
          .accessibilityLabel("Account and settings")
        }
      }
      .task { await model.appeared() }
      .task { await model.pollWhileBusy() }
      .task(id: query.trimmingCharacters(in: .whitespacesAndNewlines)) { await model.search(query) }
      .onChange(of: app.libraryVersion) { Task { await model.reload() } }
      .sheet(item: $noteTarget) { item in
        NoteEditor(itemId: item.id, title: item.title ?? Format.domain(item.url)) {
          await model.reload()
        }
      }
      .sheet(isPresented: $showSettings) { SettingsView() }
      .sheet(isPresented: $showSaveLink) {
        SaveLinkSheet { await model.reload() }
      }
  }

  @ViewBuilder private var content: some View {
    if model.searchActive && query.count >= 2 {
      SearchResultsView(query: model.searchQuery, hits: model.hits, isSearching: model.isSearching, error: model.searchError)
    } else {
      wall
    }
  }

  /// Date sections, each a two-column wall of cards. The scroll view is there
  /// from the first frame, with loading and empty states inside it: swapping
  /// it in after the search bar attached left the large title half collapsed.
  private var wall: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 12) {
        if !app.queued.isEmpty {
          QueuedShares(shares: app.queued, sending: app.isSendingQueue) { app.sendQueue() }
        }
        if !model.loaded {
          ProgressView().frame(maxWidth: .infinity).padding(.top, 120)
        } else if let error = model.error, model.items.isEmpty {
          ErrorNotice(title: "Couldn’t load your library", detail: error.localizedDescription) {
            Task { await model.reload() }
          }
          .padding(.top, 4)
        } else if model.items.isEmpty && model.filter == .all {
          ContentUnavailableView {
            Label("Your library is empty", systemImage: "books.vertical")
          } description: {
            Text("Share any link to Memory Dump from another app, or tap + to paste one.")
          }
          .padding(.top, 60)
        } else {
          cards
        }
      }
      .padding(.horizontal, 16)
      .padding(.bottom, 24)
    }
    .scrollDismissesKeyboard(.immediately)
    .refreshable { await model.refreshAll() }
  }

  @ViewBuilder private var cards: some View {
    chips.padding(.horizontal, -16)
    if model.filter == .all {
      Rediscover(pool: model.pool)
    }
    if model.items.isEmpty {
      Text("Nothing saved matches this filter.")
        .font(.callout)
        .foregroundStyle(Palette.ink2)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }
    ForEach(model.sections) { section in
      HStack(alignment: .firstTextBaseline) {
        Text(section.title).font(.display(21, .heavy, relativeTo: .title2)).foregroundStyle(Palette.ink)
        Spacer()
        Text(Format.plural(section.items.count, "item")).font(.footnote).foregroundStyle(Palette.ink3)
      }
      .padding(.top, 8)
      .accessibilityAddTraits(.isHeader)
      // iPad and other wide layouts get three columns.
      Masonry(items: section.items, columns: sizeClass == .regular ? 3 : 2, estimate: ItemCardView.estimatedHeight) { item in
        ItemCardView(
          item: item,
          onOpen: { router.push(.item(item.id)) },
          onNote: { noteTarget = item },
          onRetry: { Task { await model.retry(item) } },
          onDelete: { Task { await model.delete(item) } }
        )
        .onAppear {
          if item.id == model.items.last?.id { Task { await model.loadMore() } }
        }
      }
    }
    if model.isLoadingMore {
      ProgressView().frame(maxWidth: .infinity).padding(.vertical, 12)
    }
  }

  private var chips: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        Chip(label: "All", selected: model.filter == .all) { Task { await model.setFilter(.all) } }
        if model.sources.count > 1 {
          ForEach(model.sources, id: \.self) { s in
            Chip(label: "\(Format.sourceKind(s).label)s", dot: Palette.sourceDot(s), selected: model.filter == .source(s)) {
              Task { await model.setFilter(.source(s)) }
            }
          }
        }
        ForEach(model.tags.prefix(Self.maxTagChips), id: \.tag) { t in
          Chip(label: Format.humanizeTag(t.tag), count: t.count, selected: model.filter == .tag(t.tag)) {
            Task { await model.setFilter(.tag(t.tag)) }
          }
        }
      }
      .padding(.horizontal, 16)
      .padding(.vertical, 2)
    }
  }
}
