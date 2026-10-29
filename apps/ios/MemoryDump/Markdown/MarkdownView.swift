import SwiftUI

/// One block of a markdown document: a heading, paragraph, list item, quote,
/// code block or rule. Inline styles and links stay in `text`.
struct MarkdownBlock: Identifiable, Equatable {
  enum Kind: Equatable {
    case heading(Int)
    case paragraph
    case listItem(marker: String, depth: Int)
    case quote
    case code
    case rule
  }

  let id: Int
  let kind: Kind
  var text: AttributedString
}

enum MarkdownBlocks {
  /// Parses with Foundation's markdown parser, then groups runs into blocks by
  /// their innermost block intent.
  static func parse(_ markdown: String) -> [MarkdownBlock] {
    let options = AttributedString.MarkdownParsingOptions(
      allowsExtendedAttributes: false, interpretedSyntax: .full,
      failurePolicy: .returnPartiallyParsedIfPossible)
    guard let doc = try? AttributedString(markdown: markdown, options: options) else {
      return [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(markdown))]
    }
    var blocks: [MarkdownBlock] = []
    var lastIdentity: Int?
    for run in doc.runs {
      let intent = run.presentationIntent
      let identity = intent?.components.first?.identity ?? -1
      let piece = AttributedString(doc[run.range])
      if identity == lastIdentity, !blocks.isEmpty {
        blocks[blocks.count - 1].text.append(piece)
      } else {
        blocks.append(MarkdownBlock(id: blocks.count, kind: kind(of: intent), text: piece))
        lastIdentity = identity
      }
    }
    return blocks
  }

  private static func kind(of intent: PresentationIntent?) -> MarkdownBlock.Kind {
    // Components run from the innermost block outward.
    guard let components = intent?.components else { return .paragraph }
    for c in components {
      switch c.kind {
      case .header(let level): return .heading(level)
      case .codeBlock: return .code
      case .thematicBreak: return .rule
      default: continue
      }
    }
    let listItems = components.indices.filter {
      if case .listItem = components[$0].kind { true } else { false }
    }
    if let i = listItems.first, case .listItem(let ordinal) = components[i].kind {
      let ordered = components.indices.contains(i + 1) && components[i + 1].kind == .orderedList
      return .listItem(marker: ordered ? "\(ordinal)." : "•", depth: listItems.count - 1)
    }
    if components.contains(where: { $0.kind == .blockQuote }) { return .quote }
    return .paragraph
  }
}

/// Draws markdown in the reading style. Links go through the environment's
/// `openURL`, which the Router handles (see MemoryDumpApp).
struct MarkdownView: View {
  let blocks: [MarkdownBlock]
  var bodyFont: Font = .readingBody
  var lineSpacing: CGFloat = 9

  init(_ markdown: String, bodyFont: Font = .readingBody, lineSpacing: CGFloat = 9) {
    self.blocks = MarkdownBlocks.parse(markdown)
    self.bodyFont = bodyFont
    self.lineSpacing = lineSpacing
  }

  init(blocks: [MarkdownBlock]) {
    self.blocks = blocks
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      ForEach(blocks) { block in
        BlockView(block: block, bodyFont: bodyFont, lineSpacing: lineSpacing)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .tint(Palette.accent)
  }
}

private struct BlockView: View {
  let block: MarkdownBlock
  let bodyFont: Font
  let lineSpacing: CGFloat

  var body: some View {
    switch block.kind {
    case .heading(let level):
      Text(styled(block.text))
        .font(level <= 1 ? .serif(26, .semibold, relativeTo: .title) : .serif(22, .semibold, relativeTo: .title2))
        .foregroundStyle(Palette.ink)
        .padding(.top, 6)
    case .paragraph:
      Text(styled(block.text)).font(bodyFont).lineSpacing(lineSpacing).foregroundStyle(Palette.inkReading)
    case .listItem(let marker, let depth):
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        Text(marker).font(bodyFont).foregroundStyle(Palette.ink2)
        Text(styled(block.text)).font(bodyFont).lineSpacing(lineSpacing).foregroundStyle(Palette.inkReading)
      }
      .padding(.leading, CGFloat(depth) * 18)
    case .quote:
      Text(styled(block.text))
        .font(bodyFont).italic().lineSpacing(lineSpacing)
        .foregroundStyle(Palette.ink2)
        .padding(.leading, 14)
        .overlay(alignment: .leading) { Rectangle().fill(Palette.hairlineStrong).frame(width: 3) }
    case .code:
      ScrollView(.horizontal, showsIndicators: false) {
        Text(block.text).font(.system(.callout, design: .monospaced)).foregroundStyle(Palette.ink)
      }
      .padding(12)
      .background(Palette.surfaceMuted, in: .rect(cornerRadius: 10))
    case .rule:
      Rectangle().fill(Palette.hairlineStrong).frame(height: 1).padding(.vertical, 6)
    }
  }

  /// Citation links ("1", "2") draw as small accent numbers, not underlined text.
  private func styled(_ text: AttributedString) -> AttributedString {
    var out = text
    for run in out.runs {
      guard let link = run.link, link.absoluteString.hasPrefix(LinkRewriter.citePrefix) else { continue }
      out[run.range].font = .system(size: 12, weight: .bold)
      out[run.range].baselineOffset = 5
      out[run.range].foregroundColor = Palette.accent
      out[run.range].underlineStyle = nil
    }
    return out
  }
}
