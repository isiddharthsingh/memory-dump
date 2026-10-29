import SwiftUI

/// A string literal passed to TextField is a LocalizedStringKey, which turns
/// "you@example.com" and URLs into blue links. A String is shown as it is.
func verbatim(_ s: String) -> String { s }

/// Thumbnail when there is one, otherwise a quiet tile that names the source.
struct SourceThumb: View {
  let source: String
  let url: String?
  var thumbnailURL: String?
  var size: CGFloat = 56

  var body: some View {
    let shape = RoundedRectangle(cornerRadius: size >= 48 ? 12 : 8)
    Group {
      if let thumbnailURL, let url = URL(string: thumbnailURL) {
        CachedImage(url: url) { Palette.fill }
      } else if source == "youtube" || source == "twitter" {
        Image(systemName: Format.sourceKind(source).symbol)
          .font(.system(size: size * 0.32, weight: .semibold))
          .foregroundStyle(.white)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(source == "youtube" ? Palette.video : Palette.ink)
      } else if source == "instagram" {
        Image(systemName: Format.sourceKind(source).symbol)
          .font(.system(size: size * 0.34, weight: .semibold))
          .foregroundStyle(Swatch.lilac.ink)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .background(Swatch.lilac.fill)
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

/// A source's color, covered by its thumbnail once that loads. Takes the size
/// it is given and crops the image to it.
struct ThumbnailFill: View {
  let url: String?
  let fallback: Color

  var body: some View {
    fallback
      .overlay {
        if let url, let image = URL(string: url) {
          CachedImage(url: image) { fallback }
        }
      }
      .clipped()
  }
}

/// AsyncImage that keeps what it downloads on disk and shows it from there,
/// so thumbnails still show with no connection. Fills and crops like
/// `.resizable().scaledToFill()`.
struct CachedImage<Placeholder: View>: View {
  let url: URL
  @ViewBuilder let placeholder: Placeholder
  @State private var image: UIImage?

  var body: some View {
    Group {
      if let image {
        Image(uiImage: image).resizable().scaledToFill()
      } else {
        placeholder
      }
    }
    .task(id: url) {
      guard let (data, response) = try? await ImageCache.session.data(from: url) else { return }
      // Under this cache policy a cached error would come back forever.
      if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
        ImageCache.forget(url)
        return
      }
      image = await UIImage(data: data)?.byPreparingForDisplay()
    }
  }
}

enum ImageCache {
  private static let cache = URLCache(
    memoryCapacity: 32 << 20, diskCapacity: 300 << 20,
    directory: URL.cachesDirectory.appending(path: "Thumbnails"))

  /// A thumbnail doesn't change once saved, so a stored copy is always used.
  static let session: URLSession = {
    let config = URLSessionConfiguration.default
    config.urlCache = cache
    config.requestCachePolicy = .returnCacheDataElseLoad
    return URLSession(configuration: config)
  }()

  static func forget(_ url: URL) {
    cache.removeCachedResponse(for: URLRequest(url: url))
  }

  static func clear() {
    cache.removeAllCachedResponses()
  }
}

struct StatusBadge: View {
  let status: String
  /// For dark cards: butter on a translucent wash.
  var onDark = false

  var body: some View {
    HStack(spacing: 6) {
      Circle().fill(onDark ? Swatch.butter.fill : Palette.processingDot).frame(width: 7, height: 7)
      Text(status == "pending" ? "Saving…" : "Analyzing…")
        .font(.footnote.weight(.semibold))
        .foregroundStyle(onDark ? Swatch.butter.fill : Palette.processing)
    }
    .padding(.horizontal, 8)
    .padding(.vertical, 3)
    .background(onDark ? Swatch.butter.fill.opacity(0.16) : Palette.processingSoft, in: .capsule)
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

/// A white pill, ink when selected. `dot` marks a source or topic color;
/// `symbol` puts an accent icon in front, for suggested questions.
struct Chip: View {
  let label: String
  var count: Int?
  var dot: Color?
  var symbol: String?
  var selected = false
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 7) {
        if let dot {
          Circle().fill(dot).frame(width: 10, height: 10)
            .overlay { Circle().strokeBorder(Palette.ink.opacity(0.18)) }
        }
        if let symbol { Image(systemName: symbol).font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent) }
        Text(label)
        if let count { Text("\(count)").foregroundStyle(selected ? .white.opacity(0.7) : Palette.ink3) }
      }
      .font(.subheadline.weight(.semibold))
      .foregroundStyle(selected ? .white : Palette.ink)
      .padding(.horizontal, 14)
      .padding(.vertical, 8)
      .frame(minHeight: 40)
      // A capsule on one line; a label long enough to wrap keeps round corners
      // instead of turning into a stadium.
      .background(selected ? Palette.ink : Palette.card, in: .rect(cornerRadius: 20))
      .overlay { RoundedRectangle(cornerRadius: 20).strokeBorder(selected ? .clear : Palette.hairline) }
      .contentShape(.rect(cornerRadius: 20))
    }
    .buttonStyle(.plain)
  }
}

struct Eyebrow: View {
  let text: String
  let color: Color

  init(_ text: String, color: Color = Palette.ink2) {
    self.text = text
    self.color = color
  }

  var body: some View {
    Text(text.uppercased())
      .font(.caption.weight(.semibold))
      .tracking(0.6)
      .foregroundStyle(color)
  }
}

/// A topic's icon, in the topic's own color when the slug is known.
struct WikiTile: View {
  var slug: String?
  var size: CGFloat = 40

  var body: some View {
    let swatch = slug.map(Swatch.topic)
    Image(systemName: "book.closed")
      .font(.system(size: size * 0.42, weight: .semibold))
      .foregroundStyle(swatch?.ink ?? Palette.accent)
      .frame(width: size, height: size)
      .background(swatch?.fill ?? Palette.accentSoft, in: .rect(cornerRadius: size >= 40 ? 12 : 8))
  }
}

/// Cards shrink a little under the finger.
struct PressableCardStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .scaleEffect(configuration.isPressed ? 0.97 : 1)
      .animation(.snappy(duration: 0.18), value: configuration.isPressed)
  }
}

/// Columns of cards, each item going to the shortest column, so a wall of
/// mixed heights stays level. `estimate` guesses a card's height before layout.
struct Masonry<Item: Identifiable, Content: View>: View {
  let items: [Item]
  var columns = 2
  let estimate: (Item) -> CGFloat
  @ViewBuilder let content: (Item) -> Content
  var spacing: CGFloat = 10

  var body: some View {
    let split = Self.split(items, columns: columns, estimate: estimate)
    HStack(alignment: .top, spacing: spacing) {
      ForEach(split.indices, id: \.self) { c in
        LazyVStack(spacing: spacing) {
          ForEach(split[c]) { content($0) }
        }
        .frame(maxWidth: .infinity, alignment: .top)
      }
    }
  }

  static func split(_ items: [Item], columns: Int = 2, estimate: (Item) -> CGFloat) -> [[Item]] {
    var split = [[Item]](repeating: [], count: max(columns, 1))
    var heights = [CGFloat](repeating: 0, count: split.count)
    for item in items {
      // min(by:) returns the first of equal heights, so ties go left.
      let c = heights.indices.min { heights[$0] < heights[$1] }!
      split[c].append(item)
      heights[c] += estimate(item)
    }
    return split
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
      for (index, size) in zip(row.indices, row.sizes) {
        subviews[index].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: ProposedViewSize(size))
        x += size.width + spacing
      }
    }
  }

  /// Its natural size, or wrapped to the row when it's wider than the row.
  private func size(of view: LayoutSubview, in width: CGFloat) -> CGSize {
    let ideal = view.sizeThatFits(.unspecified)
    return ideal.width <= width ? ideal : view.sizeThatFits(ProposedViewSize(width: width, height: nil))
  }

  private struct Row {
    var indices: [Int] = []
    var sizes: [CGSize] = []
    var y: CGFloat = 0
    var width: CGFloat = 0
    var height: CGFloat = 0
  }

  private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
    var rows: [Row] = [Row()]
    for (i, view) in subviews.enumerated() {
      let size = size(of: view, in: width)
      if rows[rows.count - 1].width + size.width > width, !rows[rows.count - 1].indices.isEmpty {
        let last = rows[rows.count - 1]
        rows.append(Row(y: last.y + last.height + spacing))
      }
      rows[rows.count - 1].indices.append(i)
      rows[rows.count - 1].sizes.append(size)
      rows[rows.count - 1].width += size.width + spacing
      rows[rows.count - 1].height = max(rows[rows.count - 1].height, size.height)
    }
    return rows
  }
}
