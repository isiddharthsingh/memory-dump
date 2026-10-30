import Foundation
import SwiftUI
import Testing

@testable import MemoryDump

@Suite struct LinkRewriterTests {
  let a = "11111111-1111-1111-1111-111111111111"
  let b = "22222222-2222-2222-2222-222222222222"

  @Test func ordersCitationsByFirstUseThenUncitedSources() {
    let md = "x [item:\(b)] y [item:\(a)] z [item:\(b)]"
    let extra = UUID()
    let order = LinkRewriter.citationOrder(md, sourceIds: [UUID(uuidString: a)!, extra])
    #expect(order == [b, a, extra.lower])
  }

  @Test func rewritesWikiPage() {
    let md = "# Large Language Models\nSee [[llm-scaling]] and [[unknown-topic]].\nA claim [item:\(a)]."
    let out = LinkRewriter.wiki(
      md, title: "Large Language Models", order: [a], titles: ["llm-scaling": "llm scaling"])
    #expect(!out.contains("# Large"))
    #expect(out.contains("[LLM Scaling](memorydump://wiki/llm-scaling)"))
    #expect(out.contains("[Unknown Topic](memorydump://wiki/unknown-topic)"))
    #expect(out.contains("A claim[\u{202F}1\u{202F}](memorydump://cite/item:\(a))."))
  }

  @Test func numbersAnswerSourcesInOrderOfUse() {
    let answer = "First [wiki:llm-scaling] then [item:\(a)] again [wiki:llm-scaling]."
    let citations = [
      Citation(kind: "item", id: UUID(uuidString: b)!, title: "B", url: nil, slug: nil),
      Citation(kind: "item", id: UUID(uuidString: a)!, title: "A", url: nil, slug: nil),
      Citation(kind: "topic_page", id: UUID(), title: "LLM scaling", url: nil, slug: "llm-scaling"),
    ]
    let (order, sources) = LinkRewriter.numberSources(answer: answer, citations: citations)
    #expect(sources.map(\.n) == [1, 2, 3])
    #expect(sources.map(\.citation.title) == ["LLM scaling", "A", "B"])
    let md = LinkRewriter.answer(answer, order: order)
    let t = "\u{202F}"
    #expect(md == "First [\(t)1\(t)](memorydump://cite/wiki:llm-scaling) then [\(t)2\(t)](memorydump://cite/item:\(a)) again [\(t)1\(t)](memorydump://cite/wiki:llm-scaling).")
  }

  @Test func routerOpensAppLinks() {
    let router = Router()
    _ = router.open(URL(string: "memorydump://wiki/llm-scaling")!)
    _ = router.open(URL(string: "memorydump://cite/item:\(a)")!)
    _ = router.open(URL(string: "https://example.com")!)
    #expect(router.libraryPath == [.wiki("llm-scaling"), .item(UUID(uuidString: a)!)])
  }
}

@Suite struct FormatTests {
  @Test func domainAndPath() {
    #expect(Format.domain("https://www.karpathy.github.io/2019/04/25/recipe/") == "karpathy.github.io")
    #expect(Format.domain("file://3f2a9c1e-0000-4000-8000-000000000000/report.pdf") == "report.pdf")
    #expect(Format.domain("file://notes.png") == "notes.png")
    #expect(Format.path("https://a.com/") == "")
    #expect(Format.path("https://a.com/x/y?q=1") == "/x/y")
  }

  @Test func titlesAndTags() {
    #expect(Format.topicTitle("llm-scaling-laws") == "LLM Scaling Laws")
    #expect(Format.topicTitle("Llm Scaling") == "LLM Scaling")
    #expect(Format.humanizeTag("neural-network-training") == "Neural network training")
    #expect(Format.humanizeTag("ai-agents") == "AI agents")
  }

  @Test func initials() {
    #expect(Format.initials("Siddharth Singh") == "SS")
    #expect(Format.initials("jane.doe@example.com") == "JD")
    #expect(Format.initials("x@example.com") == "X")
  }

  @Test func friendlyErrors() {
    #expect(Format.friendlyIngestError("ConnectError: [Errno -2] Name or service not known") == "Couldn’t reach this site")
    #expect(Format.friendlyIngestError("HTTPStatusError: 403 Forbidden") == "The site blocked access")
    #expect(Format.friendlyIngestError("NotPublic: 169.254.169.254") == "This link points to a private network")
    #expect(Format.friendlyIngestError("anything", url: "file://a.pdf") == "Couldn’t process this file")
    #expect(Format.friendlyAskError(APIError.http(429, "rate limit")).title == "Hourly limit reached")
    #expect(Format.friendlyAskError(URLError(.notConnectedToInternet)).title == "Can’t reach your server")
  }

  @Test func readerContentPicksTheRichestSection() {
    let raw = "Title: T\n\nSummary: short\n\nBody:\n\(String(repeating: "word ", count: 20))\n\nUser note:\nmine"
    let r = ReaderContent(rawText: raw)
    #expect(r.body?.hasPrefix("word word") == true)
    #expect(r.note == "mine")
    #expect(!r.isStub)
    #expect(ReaderContent(rawText: "URL: https://a.com").isStub)
  }

  @Test func readerContentReadsPostsAndVideos() {
    let post = ReaderContent(rawText: "Author: Maya Okafor\n\nTweet:\nShipped my first solo iOS app last month.")
    #expect(post.body == "Shipped my first solo iOS app last month.")
    #expect(post.bodySection == "Tweet")
    #expect(post.post == "Shipped my first solo iOS app last month.")
    #expect(post.author == "Maya Okafor")
    // A post linking to a video: the post stays the post, the transcript is the body,
    // and the byline is the post's author rather than the video's channel.
    let linked = ReaderContent(
      rawText: "Author: Simon Willison\n\nTweet:\nSpotted this\n\nLink: https://youtu.be/x\n\nChannel: De Staat\n\nTranscript:\nmusic")
    #expect(linked.post == "Spotted this")
    #expect(linked.bodySection == "Transcript")
    #expect(linked.author == "Simon Willison")
    let video = ReaderContent(rawText: "Title: T\n\nChannel: Home Barista Lab\n\nTranscript:\nhello")
    #expect(video.bodySection == "Transcript")
    #expect(video.author == "Home Barista Lab")
  }

  @Test func previewCutsAtAParagraph() {
    let para = String(repeating: "a", count: 500)
    let body = "\(para)\n\n\(para)\n\n\(para)\n\n\(para)"
    let preview = ItemReaderModel.preview(of: body)
    #expect(preview == "\(para)\n\n\(para)")
    #expect(ItemReaderModel.preview(of: "short") == nil)
  }
}

@Suite struct MarkdownBlocksTests {
  @Test func groupsRunsIntoBlocks() {
    let blocks = MarkdownBlocks.parse("# Title\n\nSome **bold** text.\n\n- one\n- two\n\n1. first\n\n> quoted")
    #expect(blocks.map(\.kind) == [
      .heading(1), .paragraph, .listItem(marker: "•", depth: 0), .listItem(marker: "•", depth: 0),
      .listItem(marker: "1.", depth: 0), .quote,
    ])
    #expect(String(blocks[1].text.characters) == "Some bold text.")
  }

  @Test func keepsAppLinks() {
    let blocks = MarkdownBlocks.parse("See [LLM](memorydump://wiki/llm).")
    let link = blocks.first?.text.runs.compactMap(\.link).first
    #expect(link?.absoluteString == "memorydump://wiki/llm")
  }
}

@Suite struct RediscoverTests {
  @Test func seededIsStableAndInRange() {
    let v = Rediscover.seeded("abc#0")
    #expect(v == Rediscover.seeded("abc#0"))
    #expect((0..<1).contains(v))
  }
}

@Suite struct MoodboardTests {
  struct Card: Identifiable {
    let id: Int
    let height: CGFloat
  }

  @Test func masonryPutsEachCardInTheShorterColumn() {
    let cards = [Card(id: 0, height: 200), Card(id: 1, height: 100), Card(id: 2, height: 100), Card(id: 3, height: 50)]
    let columns = Masonry<Card, EmptyView>.split(cards, estimate: \.height)
    #expect(columns.map { $0.map(\.id) } == [[0, 3], [1, 2]])
  }

  @Test func masonrySpreadsAcrossThreeColumns() {
    let cards = (0..<4).map { Card(id: $0, height: 100) }
    let columns = Masonry<Card, EmptyView>.split(cards, columns: 3, estimate: \.height)
    #expect(columns.map { $0.map(\.id) } == [[0, 3], [1], [2]])
  }

  /// A chip longer than its row wraps inside it instead of running off the edge.
  @Test func flowLayoutWrapsAChipWiderThanTheRow() {
    let row = CGSize(width: 200, height: CGFloat.infinity)
    let long = UIHostingController(rootView: FlowLayout { Chip(label: "What do I know about LLM Knowledge Base Systems?") {} })
    let short = UIHostingController(rootView: FlowLayout { Chip(label: "Short") {} })
    #expect(long.sizeThatFits(in: row).height > short.sizeThatFits(in: row).height)
  }

  @Test func sourceComesFromTheHost() {
    #expect(Format.source(ofURL: "https://www.youtube.com/watch?v=1") == "youtube")
    #expect(Format.source(ofURL: "https://youtu.be/1") == "youtube")
    #expect(Format.source(ofURL: "https://x.com/a/status/1") == "twitter")
    #expect(Format.source(ofURL: "https://notyoutube.com/x") == "web")
    #expect(Format.source(ofURL: nil) == "web")
  }

  @Test func cardStyleFollowsTheSource() {
    #expect(ItemCardView.style(source: "image", url: "file://1/a.png", hasImage: true) == .file)
    #expect(ItemCardView.style(source: "webpage", url: "file://1/a.pdf", hasImage: false) == .file)
    #expect(ItemCardView.style(source: "youtube", url: "https://youtu.be/1", hasImage: true) == .video)
    #expect(ItemCardView.style(source: "reddit", url: "https://reddit.com/r/a/1", hasImage: true) == .post)
    #expect(ItemCardView.style(source: "instagram", url: "https://instagram.com/reel/1", hasImage: false) == .reel)
    #expect(ItemCardView.style(source: "webpage", url: "https://a.com/x", hasImage: true) == .article(image: true))
    #expect(ItemCardView.style(source: "webpage", url: "https://a.com/x", hasImage: false) == .article(image: false))
  }

  @Test func topicColorIsStablePerSlug() {
    #expect(Swatch.topic("sourdough") == Swatch.topic("sourdough"))
  }

  @Test func topicColorsSpreadAcrossThePalette() {
    let slugs = ["sourdough", "espresso", "fermentation", "vector-search", "indie-app-marketing", "knife-skills"]
    #expect(Set(slugs.map(Swatch.topic)).count >= 4)
  }
}

@Suite struct DarkModeTests {
  private func hex(_ color: Color, _ style: UIUserInterfaceStyle) -> UInt32 {
    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
    UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: style)).getRed(&r, green: &g, blue: &b, alpha: &a)
    return UInt32((r * 255).rounded()) << 16 | UInt32((g * 255).rounded()) << 8 | UInt32((b * 255).rounded())
  }

  /// WCAG contrast of two colors in one mode.
  private func contrast(_ a: Color, _ b: Color, _ style: UIUserInterfaceStyle) -> Double {
    func luminance(_ c: Color) -> Double {
      let v = hex(c, style)
      let f = { (x: UInt32) -> Double in
        let c = Double(x) / 255
        return c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
      }
      return 0.2126 * f(v >> 16 & 0xFF) + 0.7152 * f(v >> 8 & 0xFF) + 0.0722 * f(v & 0xFF)
    }
    let (x, y) = (luminance(a), luminance(b))
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)
  }

  /// UIKit gets the color through UIColor(_:), as the navigation bar titles do,
  /// and must still see both values.
  @Test func paletteResolvesPerMode() {
    #expect(hex(Palette.ground, .light) == 0xF0EEE9)
    #expect(hex(Palette.ground, .dark) == 0x121211)
    #expect(hex(Palette.ink, .light) == 0x17171B)
    #expect(hex(Palette.ink, .dark) == 0xF2F0EB)
    #expect(hex(Swatch.butter.fill, .dark) == 0xE3C65E)
    #expect(hex(Palette.onPastel, .dark) == hex(Palette.onPastel, .light))
  }

  @Test func textReadsInDark() {
    for text in [Palette.ink, Palette.ink2, Palette.ink3, Palette.inkReading, Palette.accent, Palette.failed, Palette.ready] {
      #expect(contrast(text, Palette.card, .dark) >= 4.5)
      #expect(contrast(text, Palette.ground, .dark) >= 4.5)
    }
    for fill in [Palette.ink, Palette.accent, Palette.failed] {
      #expect(contrast(Palette.onInk, fill, .dark) >= 4.5)
      #expect(contrast(Palette.onInk, fill, .light) >= 4.5)
    }
    #expect(contrast(Palette.processing, Palette.processingSoft, .dark) >= 4.5)
    #expect(contrast(Palette.failed, Palette.failedSoft, .dark) >= 4.5)
  }

  @Test func pastelsKeepDarkTextInBothModes() {
    for swatch in [Swatch.butter, .salmon, .mint, .sky, .lilac, .pink] {
      for style in [UIUserInterfaceStyle.light, .dark] {
        #expect(contrast(Palette.onPastel, swatch.fill, style) >= 4.5)
        #expect(contrast(swatch.ink, swatch.fill, style) >= 4.5)
      }
    }
  }

  @Test func appearanceMapsToTheWindowStyle() {
    #expect(Appearance.system.style == .unspecified)
    #expect(Appearance.light.style == .light)
    #expect(Appearance.dark.style == .dark)
    #expect(Appearance(rawValue: "dark") == .dark)
  }
}
