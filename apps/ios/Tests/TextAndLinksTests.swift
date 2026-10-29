import Foundation
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
