import SwiftUI

/// The top of the reader: the card you tapped on the wall, grown to full width.
/// Articles show their photo, videos and reels a play frame, posts the dark
/// quote card with the post itself. It runs up under the status and navigation
/// bars, so the back and menu buttons sit on it.
struct ReaderHero: View {
  let item: ItemDetail
  let style: ItemCardView.Style
  /// The video's channel or the post's author.
  let author: String?
  /// What a post card shows: the post's text, or its title.
  let postText: String
  /// The height of the bars it runs under.
  let topInset: CGFloat
  let play: () -> Void

  /// Heights below the bars.
  private static let mediaHeight: CGFloat = 250
  private static let plainHeight: CGFloat = 180
  private static let shape = UnevenRoundedRectangle(bottomLeadingRadius: 34, bottomTrailingRadius: 34)

  var body: some View {
    switch style {
    case .post: post
    case .video: playable(media(fill: Palette.video, play: true))
    case .reel: playable(media(fill: Swatch.lilac.fill, play: true))
    case .file: file
    case .article(true): media(fill: Palette.sand, play: false)
    case .article(false), .failed: monogram
    }
  }

  private func playable(_ card: some View) -> some View {
    // A plain style: scaling would pull the edge-to-edge hero off the screen edges.
    Button(action: play) { card }
      .buttonStyle(.plain)
      .accessibilityLabel("Play on \(siteName)")
  }

  private func media(fill: Color, play: Bool) -> some View {
    ThumbnailFill(url: item.thumbnailUrl, fallback: fill)
      .frame(height: topInset + Self.mediaHeight)
      .overlay(alignment: .bottomTrailing) {
        if play { PlayBadge(size: 60).padding(18) }
      }
      .overlay(alignment: .bottomLeading) { sticker.padding(16) }
      .clipShape(Self.shape)
      .contentShape(Self.shape)
  }

  /// Articles without a photo get the site's first letter, large.
  private var monogram: some View {
    // Decorative, so a fixed size: it would outgrow the card at large text sizes.
    Text(Format.domain(item.url).prefix(1).uppercased())
      .font(.custom(Font.SerifWeight.medium.rawValue, fixedSize: 120))
      .foregroundStyle(Palette.ink.opacity(0.85))
      .frame(maxWidth: .infinity)
      .frame(height: Self.plainHeight)
      .padding(.top, topInset)
      .background(Palette.sand)
      .overlay(alignment: .bottomLeading) { sticker.padding(16) }
      .clipShape(Self.shape)
      .accessibilityHidden(true)
  }

  private var file: some View {
    Image(systemName: Format.sourceKind(item.source).symbol)
      .font(.system(size: 56, weight: .semibold))
      .foregroundStyle(Swatch.sky.ink)
      .frame(maxWidth: .infinity)
      .frame(height: Self.plainHeight)
      .padding(.top, topInset)
      .background(Swatch.sky.fill)
      .overlay(alignment: .bottomLeading) { sticker.padding(16) }
      .clipShape(Self.shape)
      .accessibilityHidden(true)
  }

  private var post: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 11) {
        if let author {
          Text(Format.initials(author.replacing(/^u\//, with: "")))
            .font(.subheadline.weight(.heavy))
            .foregroundStyle(Palette.ink)
            .frame(width: 40, height: 40)
            .background(Swatch.topic(author).fill, in: .circle)
            .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 1) {
            Text(author).font(.callout.weight(.bold)).foregroundStyle(.white)
            if let handle { Text(handle).font(.subheadline).foregroundStyle(.white.opacity(0.75)) }
          }
        } else if let handle {
          Text(handle).font(.callout.weight(.bold)).foregroundStyle(.white)
        }
        Spacer(minLength: 0)
        Text(item.source == "twitter" ? "X" : "r/")
          .font(.footnote.weight(.heavy))
          .foregroundStyle(Palette.ink)
          .frame(width: 30, height: 30)
          .background(.white, in: .circle)
          .accessibilityHidden(true)
      }
      Text(postText)
        .font(.display(22, .semibold, relativeTo: .title3))
        .foregroundStyle(.white)
        .lineSpacing(3)
        .accessibilityAddTraits(.isHeader)
      // Reddit image posts carry their picture as the thumbnail.
      if item.thumbnailUrl != nil {
        ThumbnailFill(url: item.thumbnailUrl, fallback: .white.opacity(0.08))
          .frame(height: 200)
          .clipShape(.rect(cornerRadius: 20))
          .accessibilityHidden(true)
      }
      Text("\(Format.domain(item.url)) · Saved \(Format.relativeTime(item.createdAt))")
        .font(.footnote)
        .foregroundStyle(.white.opacity(0.75))
    }
    .padding(EdgeInsets(top: topInset + 18, leading: 22, bottom: 24, trailing: 22))
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.ink, in: Self.shape)
  }

  /// A white label stuck on the card at a slight angle, naming the source.
  private var sticker: some View {
    HStack(spacing: 7) {
      Circle().fill(Palette.sourceDot(item.source)).frame(width: 9, height: 9)
      Text(siteName).lineLimit(1)
    }
    .font(.footnote.weight(.bold))
    .foregroundStyle(Palette.ink)
    .padding(.horizontal, 13)
    .frame(minHeight: 34)
    .background(.white, in: .capsule)
    .shadow(color: Palette.ink.opacity(0.15), radius: 6, y: 4)
    .rotationEffect(.degrees(-3))
  }

  private var siteName: String {
    if Format.isUploadedFile(item.url) { return Format.sourceKind(item.source).label }
    switch item.source {
    case "youtube": return "YouTube"
    case "instagram": return "Instagram"
    default: return Format.domain(item.url)
    }
  }

  /// "@handle" from an x.com link; links without one ("/i/status/…") give nil.
  private var handle: String? {
    guard item.source == "twitter", let first = Format.path(item.url).split(separator: "/").first, first != "i"
    else { return nil }
    return "@\(first)"
  }
}

/// A related save as a small copy of its card on the wall.
struct MiniCard: View {
  let item: ItemSummary

  var body: some View {
    let style = ItemCardView.style(source: item.source, url: item.url, hasImage: item.thumbnailUrl != nil)
    Group {
      if case .post = style {
        VStack(alignment: .leading, spacing: 8) {
          Text(item.source == "twitter" ? "X" : "r/")
            .font(.caption2.weight(.heavy))
            .foregroundStyle(Palette.ink)
            .frame(width: 22, height: 22)
            .background(.white, in: .circle)
          Text(title).font(.display(15, .semibold, relativeTo: .subheadline)).foregroundStyle(.white).lineLimit(5)
          Text(meta).font(.caption2).foregroundStyle(.white.opacity(0.75)).lineLimit(1)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.ink)
      } else {
        VStack(alignment: .leading, spacing: 0) {
          band(style)
          VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.display(14, .bold, relativeTo: .subheadline)).foregroundStyle(Palette.ink).lineLimit(3)
            Text(meta).font(.caption2).foregroundStyle(Palette.ink2).lineLimit(1)
          }
          .multilineTextAlignment(.leading)
          .padding(EdgeInsets(top: 9, leading: 11, bottom: 12, trailing: 11))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card)
      }
    }
    .frame(width: 150)
    .clipShape(.rect(cornerRadius: 20))
  }

  var title: String { item.title ?? Format.domain(item.url) }
  var meta: String { Format.sourceMeta(item.url, item.source) }

  private func band(_ style: ItemCardView.Style) -> some View {
    let fill: Color =
      switch style {
      case .video: Palette.video
      case .reel: Swatch.lilac.fill
      case .file: Swatch.sky.fill
      default: Palette.sand
      }
    return ThumbnailFill(url: item.thumbnailUrl, fallback: fill)
      .frame(height: 84)
      .overlay {
        if item.thumbnailUrl == nil {
          if case .file = style {
            Image(systemName: Format.sourceKind(item.source).symbol).font(.title2).foregroundStyle(Swatch.sky.ink)
          } else if case .article = style {
            Text(Format.domain(item.url).prefix(1).uppercased())
              .font(.custom(Font.SerifWeight.medium.rawValue, fixedSize: 44))
              .foregroundStyle(Palette.ink.opacity(0.85))
          }
        }
      }
      .overlay {
        switch style {
        case .video, .reel: PlayBadge(size: 36)
        default: EmptyView()
        }
      }
  }
}

/// A rounded label for tags and extracted names; people get their initials.
struct Pill: View {
  let label: String
  var fill: Color = Palette.card
  var initials: String?

  var body: some View {
    HStack(spacing: 7) {
      if let initials {
        Text(initials)
          .font(.caption2.weight(.heavy))
          .foregroundStyle(.white)
          .frame(width: 26, height: 26)
          .background(Palette.ink, in: .circle)
          .accessibilityHidden(true)
      }
      Text(label)
    }
    .font(.subheadline.weight(.semibold))
    .foregroundStyle(Palette.ink)
    .padding(.leading, initials == nil ? 14 : 4)
    .padding(.trailing, 14)
    .frame(minHeight: 34)
    .background(fill, in: .capsule)
  }
}
