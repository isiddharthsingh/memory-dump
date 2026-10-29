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
    .background(Palette.bg)
    .navigationBarTitleDisplayMode(.inline)
    .toolbar(.hidden, for: .tabBar)
    .task { await model.load() }
    .task { await model.pollWhileBusy() }
  }

  private func reader(_ it: ItemDetail) -> some View {
    let title = it.title ?? Format.domain(it.url)
    let note = it.shareText ?? model.reader.note
    let tags = it.tags ?? []
    return ScrollView {
      VStack(alignment: .leading, spacing: 0) {
        if let thumb = it.thumbnailUrl, let url = URL(string: thumb) {
          AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Palette.fill }
            .frame(height: 200)
            .frame(maxWidth: .infinity)
            .clipShape(.rect(cornerRadius: 16))
            .padding(.bottom, 16)
        }
        HStack(spacing: 8) {
          SourceThumb(source: it.source, url: it.url, size: 20)
          Text(Format.sourceMeta(it.url, it.source)).font(.footnote.weight(.medium)).foregroundStyle(Palette.ink2)
        }
        Text(title)
          .font(.readingTitle)
          .foregroundStyle(Palette.ink)
          .padding(.top, 10)
          .accessibilityAddTraits(.isHeader)
        Group {
          if let author = it.people?.first {
            Text("By \(Text(author).fontWeight(.semibold)) · Saved \(Format.relativeTime(it.createdAt))")
          } else {
            Text("Saved \(Format.relativeTime(it.createdAt))")
          }
        }
        .font(.subheadline)
        .foregroundStyle(Palette.ink2)
        .padding(.top, 8)
        if !tags.isEmpty {
          FlowLayout(spacing: 6) {
            ForEach(tags.prefix(Self.maxTags), id: \.self) { TagLabel(label: Format.humanizeTag($0)) }
            if tags.count > Self.maxTags { TagLabel(label: "+\(tags.count - Self.maxTags)") }
          }
          .padding(.top, 12)
        }

        status(it).padding(.top, 20)

        if let note {
          Button {
            noteOpen = true
          } label: {
            VStack(alignment: .leading, spacing: 6) {
              Eyebrow("Your note")
              Text(note).font(.body).foregroundStyle(Palette.ink).frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(16)
            .background(Palette.surfaceMuted, in: .rect(cornerRadius: 14))
          }
          .buttonStyle(.plain)
          .accessibilityHint("Edit your note")
          .padding(.top, 20)
        }

        if let summary = it.summary {
          VStack(alignment: .leading, spacing: 6) {
            Label("Summary", systemImage: "sparkles").font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent)
            Text(summary).font(.serif(17)).foregroundStyle(Palette.inkReading).lineSpacing(5)
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(16)
          .background(Palette.accentSoft, in: .rect(cornerRadius: 14))
          .padding(.top, 20)
        }

        if it.status == "ready" {
          article.padding(.top, 24)
        }

        connections.padding(.top, 8)
        details(it).padding(.top, 8)
      }
      .padding(.horizontal, 24)
      .padding(.top, 8)
      .padding(.bottom, 32)
    }
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Menu("More actions", systemImage: "ellipsis") {
          Button("Add note", systemImage: "square.and.pencil") { noteOpen = true }
          ShareLink(item: it.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
          Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete = true }
        }
      }
      ToolbarItemGroup(placement: .bottomBar) {
        Button {
          if let url = URL(string: it.url) { openURL(url) }
        } label: {
          Label("Open original", systemImage: "arrow.up.right.square").labelStyle(.titleAndIcon)
        }
        .disabled(Format.isUploadedFile(it.url))
        Spacer()
        Button("Add a note", systemImage: "square.and.pencil") { noteOpen = true }
        Button("Ask about this item", systemImage: "sparkles") {
          router.ask("What are the key ideas in “\(title)”?")
        }
        .tint(Palette.accent)
        ShareLink(item: it.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
      }
    }
    .confirmationDialog("Delete this item?", isPresented: $confirmDelete, titleVisibility: .visible) {
      Button("Delete", role: .destructive) {
        Task { if await model.delete() { Haptics.success(); dismiss() } }
      }
    }
    .sheet(isPresented: $noteOpen) {
      NoteEditor(itemId: it.id, title: title) { await model.load() }
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
    }
  }

  @ViewBuilder private var article: some View {
    if !model.fullBlocks.isEmpty && !model.reader.isStub {
      VStack(alignment: .leading, spacing: 16) {
        MarkdownView(blocks: model.previewBlocks != nil && !expanded ? model.previewBlocks! : model.fullBlocks)
        if model.previewBlocks != nil {
          Button(expanded ? "Show less" : "Continue reading") { expanded.toggle() }
            .buttonStyle(.bordered)
            .tint(Palette.ink)
            .frame(maxWidth: .infinity)
        }
      }
    } else {
      Text("There’s no readable version of this page. It may be paywalled or behind a bot wall. Open the original to read it.")
        .font(.callout)
        .foregroundStyle(Palette.ink2)
    }
  }

  @ViewBuilder private var connections: some View {
    if let related = model.related {
      if !related.inWiki.isEmpty {
        group("In your wiki") {
          ForEach(related.inWiki) { tp in
            Button { router.push(.wiki(tp.slug)) } label: {
              LinkRow(title: Format.topicTitle(tp.title)) { WikiTile() }
            }
            .buttonStyle(.plain)
          }
        }
      }
      if !related.related.isEmpty {
        group("Related") {
          ForEach(related.related.prefix(5)) { r in
            Button { router.push(.item(r.id)) } label: {
              LinkRow(title: r.title ?? Format.domain(r.url), subtitle: Format.sourceMeta(r.url, r.source)) {
                SourceThumb(source: r.source, url: r.url, thumbnailURL: r.thumbnailUrl, size: 40)
              }
            }
            .buttonStyle(.plain)
          }
        }
      }
    }
  }

  /// What the analyzer pulled out, as chips.
  @ViewBuilder private func details(_ it: ItemDetail) -> some View {
    let people = it.people ?? []
    let mentions = (it.handles ?? []) + (it.brandsProducts ?? [])
    let topics = (it.topics ?? []).map(Format.humanizeTag)
    if !(people.isEmpty && mentions.isEmpty && topics.isEmpty) {
      group(nil) {
        ForEach([("People", people), ("Mentioned", mentions), ("Topics", topics)], id: \.0) { label, values in
          if !values.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
              Eyebrow(label)
              FlowLayout(spacing: 6) { ForEach(values, id: \.self) { TagLabel(label: $0) } }
            }
            .padding(.bottom, 8)
          }
        }
      }
    }
  }

  private func group<Content: View>(_ title: String?, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      Divider().padding(.bottom, 8)
      if let title { Eyebrow(title) }
      content()
    }
    .padding(.top, 16)
  }
}
