import SwiftUI

struct LibraryView: View {
  @Environment(AppModel.self) private var app
  @Environment(Router.self) private var router
  @State private var model = LibraryModel()
  @State private var query = ""
  @State private var noteTarget: ItemSummary?
  @State private var showSettings = false
  @State private var showSaveLink = false

  private static let maxTagChips = 12

  var body: some View {
    content
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
              .foregroundStyle(.white)
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
    } else if !model.loaded {
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
    } else if let error = model.error, model.items.isEmpty {
      ErrorNotice(title: "Couldn’t load your library", detail: error.localizedDescription) {
        Task { await model.reload() }
      }
      .padding(20)
      .frame(maxHeight: .infinity, alignment: .top)
    } else if model.items.isEmpty && model.filter == .all {
      ContentUnavailableView {
        Label("Your library is empty", systemImage: "books.vertical")
      } description: {
        Text("Share any link to Memory Dump from another app, or tap + to paste one.")
      }
    } else {
      list
    }
  }

  private var list: some View {
    List {
      Section {
        chips
          .listRowInsets(EdgeInsets())
          .listRowSeparator(.hidden)
        if model.filter == .all {
          Rediscover(pool: model.pool)
            .listRowInsets(EdgeInsets(top: 8, leading: 20, bottom: 8, trailing: 20))
            .listRowSeparator(.hidden)
        }
      }
      if model.items.isEmpty {
        Text("Nothing saved matches this filter.")
          .font(.callout)
          .foregroundStyle(Palette.ink2)
          .frame(maxWidth: .infinity)
          .padding(.vertical, 32)
          .listRowSeparator(.hidden)
      }
      ForEach(model.sections) { section in
        Section {
          ForEach(section.items) { item in
            ItemRowView(
              item: item,
              onOpen: { router.push(.item(item.id)) },
              onNote: { noteTarget = item },
              onRetry: { Task { await model.retry(item) } },
              onDelete: { Task { await model.delete(item) } }
            )
            .listRowInsets(EdgeInsets(top: 12, leading: 20, bottom: 12, trailing: 20))
            .onAppear {
              if item.id == model.items.last?.id { Task { await model.loadMore() } }
            }
          }
        } header: {
          HStack {
            Text(section.title).font(.title3.bold()).foregroundStyle(Palette.ink)
            Spacer()
            Text(Format.plural(section.items.count, "item")).font(.footnote).foregroundStyle(Palette.ink3)
          }
          .textCase(nil)
        }
      }
      if model.isLoadingMore {
        ProgressView().frame(maxWidth: .infinity).listRowSeparator(.hidden)
      }
    }
    .listStyle(.plain)
    .scrollDismissesKeyboard(.immediately)
    .refreshable { await model.refreshAll() }
  }

  private var chips: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        Chip(label: "All", selected: model.filter == .all) { Task { await model.setFilter(.all) } }
        if model.sources.count > 1 {
          ForEach(model.sources, id: \.self) { s in
            Chip(label: "\(Format.sourceKind(s).label)s", selected: model.filter == .source(s)) {
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
      .padding(.horizontal, 20)
      .padding(.vertical, 6)
    }
  }
}
