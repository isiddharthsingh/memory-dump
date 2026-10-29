import SwiftUI

/// A string literal passed to TextField is a LocalizedStringKey, which turns
/// "you@example.com" and URLs into blue links. A String is shown as it is.
func verbatim(_ s: String) -> String { s }

/// Thumbnail when there is one, otherwise a quiet tile that names the source.
struct SourceThumb: View {
  let source: String
  let url: String?
  var thumbnailURL: String?
  var failed = false
  var size: CGFloat = 56

  var body: some View {
    let shape = RoundedRectangle(cornerRadius: size >= 48 ? 12 : 8)
    Group {
      if let thumbnailURL, !failed, let url = URL(string: thumbnailURL) {
        AsyncImage(url: url) { image in
          image.resizable().scaledToFill()
        } placeholder: {
          Palette.fill
        }
      } else if failed {
        Image(systemName: "link")
          .font(.system(size: size * 0.36, weight: .medium))
          .foregroundStyle(Palette.failed)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(Palette.failedSoft)
      } else if source == "youtube" || source == "twitter" {
        Image(systemName: Format.sourceKind(source).symbol)
          .font(.system(size: size * 0.32, weight: .semibold))
          .foregroundStyle(.white)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(Palette.ink)
      } else if let url, let letter = Format.domain(url).first, !Format.isUploadedFile(url) {
        Text(String(letter).uppercased())
          .font(.serif(size * 0.44, .medium))
          .foregroundStyle(Palette.ink2)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(Palette.fill)
      } else {
        Image(systemName: Format.sourceKind(source).symbol)
          .font(.system(size: size * 0.36))
          .foregroundStyle(Palette.ink2)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(Palette.fill)
      }
    }
    .frame(width: size, height: size)
    .clipShape(shape)
  }
}

struct StatusBadge: View {
  let status: String

  var body: some View {
    HStack(spacing: 6) {
      Circle().fill(Palette.processingDot).frame(width: 7, height: 7)
      Text(status == "pending" ? "Saving…" : "Analyzing…")
        .font(.footnote.weight(.semibold))
        .foregroundStyle(Palette.processing)
    }
    .padding(.horizontal, 8)
    .padding(.vertical, 3)
    .background(Palette.processingSoft, in: .capsule)
  }
}

struct ErrorNotice: View {
  let title: String
  var detail: String?
  var retry: (() -> Void)?

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Label(title, systemImage: "exclamationmark.circle")
        .font(.headline)
        .foregroundStyle(Palette.failed)
      if let detail { Text(detail).font(.subheadline).foregroundStyle(Palette.ink2) }
      if let retry {
        Button("Try again", action: retry)
          .buttonStyle(.bordered)
          .tint(Palette.ink)
          .padding(.top, 4)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(16)
    .background(Palette.failedSoft, in: .rect(cornerRadius: 14))
  }
}

struct Chip: View {
  let label: String
  var count: Int?
  var selected = false
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 4) {
        Text(label)
        if let count { Text("\(count)").foregroundStyle(selected ? .white.opacity(0.7) : Palette.ink3) }
      }
      .font(.subheadline.weight(.medium))
      .foregroundStyle(selected ? .white : Palette.ink)
      .padding(.horizontal, 12)
      .padding(.vertical, 7)
      .background(selected ? Palette.ink : Palette.fill, in: .capsule)
    }
    .buttonStyle(.plain)
  }
}

struct TagLabel: View {
  let label: String

  var body: some View {
    Text(label)
      .font(.footnote.weight(.medium))
      .foregroundStyle(Palette.ink2)
      .padding(.horizontal, 9)
      .padding(.vertical, 4)
      .background(Palette.fill, in: .capsule)
  }
}

struct Eyebrow: View {
  let text: String

  init(_ text: String) { self.text = text }

  var body: some View {
    Text(text.uppercased())
      .font(.caption.weight(.semibold))
      .tracking(0.6)
      .foregroundStyle(Palette.ink2)
  }
}

struct WikiTile: View {
  var size: CGFloat = 40

  var body: some View {
    Image(systemName: "book.closed")
      .font(.system(size: size * 0.42))
      .foregroundStyle(Palette.accent)
      .frame(width: size, height: size)
      .background(Palette.accentSoft, in: .rect(cornerRadius: 8))
  }
}

struct LinkRow<Leading: View>: View {
  let title: String
  var subtitle: String?
  @ViewBuilder let leading: Leading

  var body: some View {
    HStack(spacing: 12) {
      leading
      VStack(alignment: .leading, spacing: 2) {
        Text(title).font(.headline).foregroundStyle(Palette.ink).lineLimit(2)
        if let subtitle { Text(subtitle).font(.footnote).foregroundStyle(Palette.ink3).lineLimit(1) }
      }
      Spacer(minLength: 0)
      Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Palette.hairlineStrong)
    }
    .contentShape(.rect)
  }
}

/// Wrapping row of views, for chips and tags.
struct FlowLayout: Layout {
  var spacing: CGFloat = 8

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
    return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: rows.last.map { $0.y + $0.height } ?? 0)
  }

  func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    for row in arrange(width: bounds.width, subviews: subviews) {
      var x = bounds.minX
      for index in row.indices {
        let size = subviews[index].sizeThatFits(.unspecified)
        subviews[index].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: .unspecified)
        x += size.width + spacing
      }
    }
  }

  private struct Row {
    var indices: [Int] = []
    var y: CGFloat = 0
    var width: CGFloat = 0
    var height: CGFloat = 0
  }

  private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
    var rows: [Row] = [Row()]
    for (i, view) in subviews.enumerated() {
      let size = view.sizeThatFits(.unspecified)
      if rows[rows.count - 1].width + size.width > width, !rows[rows.count - 1].indices.isEmpty {
        let last = rows[rows.count - 1]
        rows.append(Row(y: last.y + last.height + spacing))
      }
      rows[rows.count - 1].indices.append(i)
      rows[rows.count - 1].width += size.width + spacing
      rows[rows.count - 1].height = max(rows[rows.count - 1].height, size.height)
    }
    return rows
  }
}
