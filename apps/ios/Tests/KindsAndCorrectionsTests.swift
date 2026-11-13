import Foundation
import Testing

@testable import MemoryDump

@Suite struct KindTests {
  private static let item = #"""
    {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "url": "https://a.com/x", "source": "instagram",
     "status": "ready", "title": "Ramen crawl", "summary": null, "thumbnail_url": null, "tags": null,
     "created_at": "2026-11-01T10:00:00Z", "raw_text": null, "people": null, "handles": null,
     "brands_products": null, "topics": null, "share_text": null
    """#

  @Test func decodesAPlaceWithItsDetailsAndDoneMark() throws {
    let json =
      Self.item + #"""
        , "kind": "place", "done_at": "2026-11-02T09:30:00.123456Z",
        "details": {"places": [{"name": "Ichiran", "address": "Shibuya, Tokyo"}, {"name": "Afuri", "address": ""}]}}
        """#
    let item = try JSONDecoder.api.decode(ItemDetail.self, from: Data(json.utf8))
    #expect(item.kind == "place")
    #expect(item.isDone)
    #expect(item.details?.places?.map(\.query) == ["Ichiran, Shibuya, Tokyo", "Afuri"])
    #expect(item.details?.steps == nil)
  }

  /// A server from before kinds, and an item it hasn't classified yet.
  @Test func anItemWithoutAKindStillDecodes() throws {
    for tail in ["}", #", "kind": null, "done_at": null, "details": null}"#] {
      let item = try JSONDecoder.api.decode(ItemDetail.self, from: Data((Self.item + tail).utf8))
      #expect(item.kind == nil)
      #expect(!item.isDone)
      #expect(item.details == nil)
    }
  }

  @Test func decodesTheMapsPlaces() throws {
    let json = #"""
      [{"item": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "title": "Ramen crawl", "name": "Ichiran",
        "address": "Tokyo", "done": true}]
      """#
    let places = try JSONDecoder.api.decode([SavedPlace].self, from: Data(json.utf8))
    #expect(places.first?.query == "Ichiran, Tokyo")
    #expect(places.first?.id == "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f|Ichiran|Tokyo")
    #expect(places.first?.done == true)
  }

  @Test func everyListedKindHasAChipAndADoneLabel() {
    for kind in Format.kinds { #expect(Format.kind(kind) != nil) }
    #expect(Format.kind("other") == nil)
    #expect(Format.kind(nil) == nil)
    #expect(Format.doneLabel("place") == "Been there")
    #expect(Format.doneLabel(nil) == "Done")
  }

  @Test func aPlaceOpensInMapsByNameAndAddress() {
    let url = Format.mapsURL("Joe’s Pizza, 7 Carmine St & Bleecker")
    #expect(url?.host() == "maps.apple.com")
    let query = url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }?.queryItems
    #expect(query == [URLQueryItem(name: "q", value: "Joe’s Pizza, 7 Carmine St & Bleecker")])
  }
}

@Suite struct CorrectionTests {
  @Test func decodesAPagesCorrections() throws {
    let page = #"""
      {"id": "6f1c1b0e-6b8e-4c33-9f55-5a1b2c3d4e5f", "slug": "ramen", "title": "Ramen", "backlinks": null,
       "source_item_ids": [], "version": 3, "updated_at": "2026-11-02T09:30:00Z", "body_md": "Body."
      """#
    let corrected =
      page + #"""
        , "corrections": [{"id": "0b0e6f1c-6b8e-4c33-9f55-5a1b2c3d4e5f", "text": "It closed in 2025.",
          "at": "2026-11-01T08:00:00.000001+00:00"}]}
        """#
    let decoded = try JSONDecoder.api.decode(TopicPageDetail.self, from: Data(corrected.utf8))
    #expect(decoded.corrections?.map(\.text) == ["It closed in 2025."])
    // None yet, or a server from before corrections.
    for tail in ["}", #", "corrections": null}"#] {
      let plain = try JSONDecoder.api.decode(TopicPageDetail.self, from: Data((page + tail).utf8))
      #expect(plain.corrections == nil)
    }
  }
}

@Suite struct AskStepTests {
  @Test func aLookupArrivesAsAStepBetweenTokens() throws {
    var parser = SSEParser()
    let lines = [
      "event: step", #"data: {"text": "Listing your places"}"#,
      "event: token", #"data: {"text": "Two places."}"#,
      // A step that can't be read is only a missing status line.
      "event: step", #"data: {"words": 1}"#,
    ]
    let events = try lines.compactMap { try parser.feed($0) }
    #expect(events == [.step("Listing your places"), .token("Two places.")])
  }
}
