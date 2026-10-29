import SwiftUI

/// One saved item on the Library wall. Its shape follows the source: videos
/// get a play frame, posts a dark quote card, reels a lilac card, articles an
/// image or a pull quote. A long press opens the system menu: Add note, Share,
/// Delete. There is no swipe to delete (commit 3988179 removed it).
struct ItemCardView: View {
  let item: ItemSummary
  let onOpen: () -> Void
  let onNote: () -> Void
  let onRetry: () -> Void
  let onDelete: () -> Void

  enum Style: Equatable {
    case video, post, reel, file, failed
    case article(image: Bool)
  }

  static func style(of item: ItemSummary) -> Style {
    item.isFailed ? .failed : style(source: item.source, url: item.url, hasImage: item.thumbnailUrl != nil)
  }

  /// The shape a source takes, on the wall and at the top of the reader.
  static func style(source: String, url: String, hasImage: Bool) -> Style {
    if Format.isUploadedFile(url) { return .file }
    switch source {
    case "youtube": return .video
    case "twitter", "reddit": return .post
    case "instagram": return .reel
    case "pdf", "image": return .file
    default: return .article(image: hasImage)
    }
  }

  /// Rough card heights at half the screen width, for balancing the columns.
  static func estimatedHeight(_ item: ItemSummary) -> CGFloat {
    switch style(of: item) {
    case .video: 190
    case .post: 150
    case .reel: 230
    case .file: 130
    case .failed: 170
    case .article(let image): image ? 220 : 170
    }
  }

  private var title: String { item.title ?? Format.domain(item.url) }
  /// Untitled items show the link's path, so two saves from one site differ.
  private var meta: String {
    item.title != nil
      ? Format.sourceMeta(item.url, item.source)
      : [Format.path(item.url), "Link"].filter { !$0.isEmpty }.joined(separator: " · ")
  }

  var body: some View {
    Button(action: onOpen) {
      card
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect(cornerRadius: 22))
    }
    .buttonStyle(PressableCardStyle())
    .contentShape(.contextMenuPreview, .rect(cornerRadius: 22))
    .contextMenu {
      Button("Add note", systemImage: "square.and.pencil", action: onNote)
      ShareLink(item: item.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
      Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
    }
    .accessibilityLabel("\(title), \(meta)\(item.isFailed ? ", failed" : "")")
    .accessibilityHint("Opens the item. Long-press for more actions.")
  }

  @ViewBuilder private var card: some View {
    switch Self.style(of: item) {
    case .video: video
    case .post: post
    case .reel: reel
    case .file: file
    case .failed: failed
    case .article(true): imageArticle
    case .article(false): quoteArticle
    }
  }

  // MARK: Styles

  private var video: some View {
    VStack(alignment: .leading, spacing: 0) {
      thumbnail(height: 104, fallback: Palette.video)
        .overlay { PlayBadge(size: 46) }
      footer
    }
    .background(Palette.card)
    .clipShape(.rect(cornerRadius: 22))
  }

  private var post: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 8) {
        Text(item.source == "twitter" ? "X" : "r/")
          .font(.caption.weight(.heavy))
          .foregroundStyle(Palette.onPastel)
          .frame(width: 24, height: 24)
          .background(.white, in: .circle)
        Text(meta).font(.caption).foregroundStyle(.white.opacity(0.75)).lineLimit(1)
      }
      Text(title)
        .font(.display(17, .semibold))
        .foregroundStyle(.white)
        .lineLimit(6)
      if item.isBusy { StatusBadge(status: item.status, onDark: true) }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.post, in: .rect(cornerRadius: 22))
    .overlay { RoundedRectangle(cornerRadius: 22).strokeBorder(Palette.postEdge) }
  }

  private var reel: some View {
    VStack(alignment: .leading, spacing: 0) {
      thumbnail(height: 150, fallback: Swatch.lilac.fill)
        .overlay { PlayBadge(size: 52) }
        .clipShape(.rect(cornerRadius: 16))
        .padding([.horizontal, .top], 8)
      VStack(alignment: .leading, spacing: 4) {
        Text(title).font(.cardTitle).foregroundStyle(Palette.onPastel).lineLimit(3)
        if item.isBusy {
          StatusBadge(status: item.status)
        } else {
          Text(meta).font(.caption).foregroundStyle(Swatch.lilac.ink).lineLimit(1)
        }
      }
      .padding(EdgeInsets(top: 10, leading: 13, bottom: 14, trailing: 13))
    }
    .background(Swatch.lilac.fill, in: .rect(cornerRadius: 22))
  }

  private var imageArticle: some View {
    VStack(alignment: .leading, spacing: 0) {
      thumbnail(height: 118, fallback: Palette.sand)
      VStack(alignment: .leading, spacing: 7) {
        if let tag = item.tags?.first {
          Text(Format.humanizeTag(tag))
            .font(.caption2.weight(.bold))
            .foregroundStyle(Palette.ink)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Palette.ground, in: .capsule)
        }
        Text(title).font(.cardTitle).foregroundStyle(Palette.ink).lineLimit(4)
        status(or: Text(Format.domain(item.url)))
      }
      .padding(EdgeInsets(top: 11, leading: 13, bottom: 14, trailing: 13))
    }
    .background(Palette.card)
    .clipShape(.rect(cornerRadius: 22))
  }

  private var quoteArticle: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("“")
        .font(.display(40, .heavy))
        .foregroundStyle(Palette.accent)
        .frame(height: 22, alignment: .top)
        .accessibilityHidden(true)
      Text(title).font(.display(17, .bold)).foregroundStyle(Palette.ink).lineLimit(5)
      if let summary = item.summary, !item.isBusy {
        Text(summary).font(.footnote).foregroundStyle(Palette.ink2).lineLimit(3)
      }
      status(or: Text(meta)).padding(.top, 2)
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.card, in: .rect(cornerRadius: 22))
  }

  private var file: some View {
    VStack(alignment: .leading, spacing: 8) {
      Image(systemName: Format.sourceKind(item.source).symbol)
        .font(.title3.weight(.semibold))
        .foregroundStyle(Swatch.sky.ink)
      Text(title).font(.cardTitle).foregroundStyle(Palette.onPastel).lineLimit(4)
      if item.isBusy {
        StatusBadge(status: item.status)
      } else {
        Text(meta).font(.caption).foregroundStyle(Swatch.sky.ink).lineLimit(1)
      }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Swatch.sky.fill, in: .rect(cornerRadius: 22))
  }

  private var failed: some View {
    VStack(alignment: .leading, spacing: 8) {
      Image(systemName: "link")
        .font(.footnote.weight(.bold))
        .foregroundStyle(Palette.failed)
        .frame(width: 30, height: 30)
        .background(Palette.failedSoft, in: .circle)
      Text(title).font(.cardTitle).foregroundStyle(Palette.ink).lineLimit(3)
      Text(Format.friendlyIngestError(item.error, url: item.url))
        .font(.footnote.weight(.medium)).foregroundStyle(Palette.failed).lineLimit(2)
      Text(Format.isUploadedFile(item.url) ? "\(meta) · Share it again to retry" : meta)
        .font(.caption).foregroundStyle(Palette.ink3).lineLimit(2)
      if !Format.isUploadedFile(item.url) {
        Button("Retry", action: onRetry)
          .font(.footnote.weight(.semibold))
          .buttonStyle(.bordered)
          .controlSize(.small)
          .tint(Palette.ink)
          .accessibilityLabel("Retry saving \(title)")
      }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.card, in: .rect(cornerRadius: 22))
    .overlay { RoundedRectangle(cornerRadius: 22).strokeBorder(Palette.failed.opacity(0.25)) }
  }

  // MARK: Parts

  private func thumbnail(height: CGFloat, fallback: Color) -> some View {
    ThumbnailFill(url: item.thumbnailUrl, fallback: fallback).frame(height: height)
  }

  private var footer: some View {
    VStack(alignment: .leading, spacing: 5) {
      Text(title).font(.cardTitle).foregroundStyle(Palette.ink).lineLimit(4)
      status(or: Text(meta))
    }
    .padding(EdgeInsets(top: 11, leading: 13, bottom: 14, trailing: 13))
  }

  /// "Saving…" or "Analyzing…" while the worker runs, else the given meta line.
  @ViewBuilder private func status(or meta: Text) -> some View {
    if item.isBusy {
      StatusBadge(status: item.status)
    } else {
      meta.font(.caption).foregroundStyle(Palette.ink2).lineLimit(1)
    }
  }
}

/// The white play button over video and reel thumbnails, the same in both modes.
struct PlayBadge: View {
  let size: CGFloat

  var body: some View {
    Image(systemName: "play.fill")
      .font(.system(size: size * 0.4))
      .foregroundStyle(Palette.onPastel)
      .offset(x: size * 0.04)
      .frame(width: size, height: size)
      .background(.white, in: .circle)
      .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
      .accessibilityHidden(true)
  }
}
