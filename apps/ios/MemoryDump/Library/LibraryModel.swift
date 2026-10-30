import Foundation

/// The Library list: pages of items, filters, Rediscover's pool and search.
@Observable
final class LibraryModel {
  enum Filter: Hashable {
    case all
    case source(String)
    case tag(String)
  }

  struct DateSection: Identifiable {
    let title: String
    let items: [ItemSummary]
    var id: String { title }
  }

  static let pageSize = 25
  private static let poolSize = 200
  private static let sourceOrder = ["web", "youtube", "twitter", "instagram", "reddit", "pdf", "image"]

  private(set) var items: [ItemSummary] = []
  private(set) var hasMore = false
  private(set) var loaded = false
  private(set) var isLoadingMore = false
  private(set) var error: Error?
  private(set) var tags: [TagCount] = []
  private(set) var pool: [ItemSummary] = []

  private(set) var searchQuery = ""
  private(set) var hits: [SearchHit] = []
  private(set) var isSearching = false
  private(set) var searchError: Error?

  var filter: Filter = .all

  private let api = APIClient.shared
  private var loadedExtras = false

  var searchActive: Bool { searchQuery.count >= 2 }

  var sections: [DateSection] {
    Self.grouped(items, by: \.createdAt).map { DateSection(title: $0.title, items: $0.values) }
  }

  var sources: [String] {
    let present = Set(pool.map(\.source))
    return Self.sourceOrder.filter(present.contains)
  }

  var subtitle: String? {
    if APIClient.shared.isOffline { return Format.offline }
    guard filter == .all, !items.isEmpty else { return nil }
    let failed = items.filter(\.isFailed).count
    var s = hasMore ? "\(items.count)+ saved" : "\(items.count) saved"
    if failed > 0 { s += " · \(failed) need\(failed == 1 ? "s" : "") attention" }
    return s
  }

  // MARK: Loading

  /// Runs each time the Library appears. Tags and the pool load once.
  func appeared() async {
    if !loadedExtras {
      loadedExtras = true
      tags = (try? await APIClient.saved.tags()) ?? []
      pool = (try? await APIClient.saved.listItems(limit: Self.poolSize, status: "ready")) ?? []
      async let t: Void = loadTags()
      async let p: Void = loadPool()
      async let r: Void = reload()
      _ = await (t, p, r)
    } else {
      await reload()
    }
  }

  func refreshAll() async {
    async let t: Void = loadTags()
    async let p: Void = loadPool()
    async let r: Void = reload()
    _ = await (t, p, r)
  }

  func setFilter(_ f: Filter) async {
    Haptics.selection()
    filter = filter == f ? .all : f
    items = []
    loaded = false
    await reload()
  }

  /// Reloads from the top, keeping as many rows as are loaded now.
  func reload() async {
    let count = max(Self.pageSize, items.count)
    let requested = filter
    // First load of this filter: last time's copy while the server is asked.
    if !loaded, let page = try? await fetch(limit: count, from: .saved), requested == filter {
      items = Self.dedupe(page)
      hasMore = page.count == count
      loaded = true
    }
    do {
      let page = try await fetch(limit: count)
      guard requested == filter else { return }
      items = Self.dedupe(page)
      hasMore = page.count == count
      error = nil
    } catch {
      if items.isEmpty { self.error = error }
    }
    loaded = true
  }

  /// The page after the last row, the same page OfflineSync saves for it.
  func loadMore() async {
    guard hasMore, !isLoadingMore, let last = items.last else { return }
    isLoadingMore = true
    defer { isLoadingMore = false }
    do {
      let page = try await fetch(limit: Self.pageSize, before: last.id)
      items = Self.dedupe(items + page)
      hasMore = page.count == Self.pageSize
    } catch where error.isUnknownCursor {
      // The last row was deleted (on another device, say): start again from the top.
      await reload()
    } catch {
    }
  }

  /// Keeps polling while something is still being saved or analyzed, so rows
  /// change from "Analyzing…" to ready with no pull to refresh.
  func pollWhileBusy() async {
    while !Task.isCancelled {
      try? await Task.sleep(for: .seconds(3))
      if items.contains(where: \.isBusy) { await reload() }
    }
  }

  private func fetch(limit: Int, before: UUID? = nil, from api: APIClient = .shared) async throws
    -> [ItemSummary]
  {
    switch filter {
    case .all: try await api.listItems(limit: limit, before: before)
    case .source(let s): try await api.listItems(limit: limit, before: before, source: s)
    case .tag(let t): try await api.listItems(limit: limit, before: before, tag: t)
    }
  }

  private func loadTags() async {
    if let t = try? await api.tags() { tags = t }
  }

  private func loadPool() async {
    if let p = try? await api.listItems(limit: Self.poolSize, status: "ready") { pool = p }
  }

  // MARK: Actions

  func retry(_ item: ItemSummary) async {
    _ = try? await api.retryItem(item.id)
    await reload()
  }

  func delete(_ item: ItemSummary) async {
    do {
      try await api.deleteItem(item.id)
      ImageCache.forget(item.thumbnailUrl)
      Haptics.success()
      items.removeAll { $0.id == item.id }
      pool.removeAll { $0.id == item.id }
      // Replaces the saved lists too, which still have it for offline use.
      await refreshAll()
    } catch {
      Haptics.error()
    }
  }

  // MARK: Search

  func search(_ raw: String) async {
    let q = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard q.count >= 2 else {
      searchQuery = ""
      hits = []
      isSearching = false
      searchError = nil
      return
    }
    try? await Task.sleep(for: .milliseconds(250))
    guard !Task.isCancelled else { return }
    searchQuery = q
    isSearching = true
    searchError = nil
    // Each keystroke cancels the previous search. URLSession reports that as
    // URLError(.cancelled), not CancellationError, so check the task instead,
    // and leave the state to the newer search.
    do {
      let result = try await api.search(q)
      guard !Task.isCancelled else { return }
      hits = Self.dedupe(result)
    } catch {
      guard !Task.isCancelled else { return }
      searchError = error
    }
    isSearching = false
  }

  // MARK: Helpers

  private static func dedupe(_ items: [ItemSummary]) -> [ItemSummary] {
    var seen = Set<UUID>()
    return items.filter { seen.insert($0.id).inserted }
  }

  /// One row per item or wiki page; the retriever can return several chunks of each.
  private static func dedupe(_ hits: [SearchHit]) -> [SearchHit] {
    var seen = Set<UUID>()
    return hits.filter { seen.insert($0.id).inserted }
  }

  static func sectionLabel(_ created: Date, now: Date) -> String {
    let days = Format.daysAgo(created, now: now)
    if days <= 0 { return "Today" }
    if days == 1 { return "Yesterday" }
    if days < 7 { return "This week" }
    let cal = Calendar.current
    if cal.isDate(created, equalTo: now, toGranularity: .month) { return "This month" }
    return created.formatted(.dateTime.month(.wide).year())
  }

  /// Buckets values under the `sectionLabel` of their date, keeping their order.
  static func grouped<T>(_ values: [T], by date: (T) -> Date, now: Date = .now) -> [(title: String, values: [T])] {
    var order: [String] = []
    var buckets: [String: [T]] = [:]
    for value in values {
      let label = sectionLabel(date(value), now: now)
      if buckets[label] == nil { order.append(label) }
      buckets[label, default: []].append(value)
    }
    return order.map { ($0, buckets[$0]!) }
  }
}
