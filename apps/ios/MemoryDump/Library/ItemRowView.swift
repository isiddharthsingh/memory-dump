import SwiftUI

/// One saved item. A long press opens the system menu: Add note, Share,
/// Delete. There is no swipe to delete (commit 3988179 removed it).
struct ItemRowView: View {
  let item: ItemSummary
  let onOpen: () -> Void
  let onNote: () -> Void
  let onRetry: () -> Void
  let onDelete: () -> Void

  private var title: String { item.title ?? Format.domain(item.url) }
  private var meta: String {
    item.title != nil
      ? Format.sourceMeta(item.url, item.source)
      : [Format.path(item.url), "Link"].filter { !$0.isEmpty }.joined(separator: " · ")
  }

  var body: some View {
    Button {
      Haptics.tap()
      onOpen()
    } label: {
      HStack(alignment: .top, spacing: 14) {
        SourceThumb(source: item.source, url: item.url, thumbnailURL: item.thumbnailUrl, failed: item.isFailed)
        VStack(alignment: .leading, spacing: 3) {
          Text(title).font(.headline).foregroundStyle(Palette.ink).lineLimit(2)
          if item.isFailed {
            HStack(spacing: 6) {
              Circle().fill(Palette.failed).frame(width: 7, height: 7)
              Text(Format.friendlyIngestError(item.error, url: item.url))
                .font(.subheadline.weight(.medium)).foregroundStyle(Palette.failed).lineLimit(1)
            }
          } else if item.isBusy {
            StatusBadge(status: item.status)
          } else if let summary = item.summary {
            Text(summary).font(.subheadline).foregroundStyle(Palette.ink2).lineLimit(1)
          }
          HStack(spacing: 8) {
            Text(item.isFailed && Format.isUploadedFile(item.url) ? "\(meta) · Share it again to retry" : meta)
              .font(.footnote).foregroundStyle(Palette.ink3).lineLimit(1)
            Spacer(minLength: 0)
            if item.isFailed && !Format.isUploadedFile(item.url) {
              Button("Retry", action: onRetry)
                .font(.footnote.weight(.semibold))
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(Palette.ink)
                .accessibilityLabel("Retry saving \(title)")
            }
          }
        }
      }
      .contentShape(.rect)
    }
    .buttonStyle(.plain)
    .contextMenu {
      Button("Add note", systemImage: "square.and.pencil", action: onNote)
      ShareLink(item: item.url, subject: Text(title)) { Label("Share", systemImage: "square.and.arrow.up") }
      Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
    }
    .accessibilityLabel("\(title), \(meta)\(item.isFailed ? ", failed" : "")")
    .accessibilityHint("Opens the item. Long-press for more actions.")
  }
}
