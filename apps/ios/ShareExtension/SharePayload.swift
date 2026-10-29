import Foundation
import UniformTypeIdentifiers

enum ShareError: LocalizedError {
  case nothingToSave

  var errorDescription: String? { "Couldn’t find a link or file in this share." }
}

/// What a share sheet item holds, reduced to the one thing to save.
enum SharePayload {
  case link(url: URL, note: String?, title: String?)
  case file(url: URL, name: String, mimeType: String, note: String?)

  /// Files first: for a PDF or image share, the file is what the user wants,
  /// even when a URL comes with it. Then a URL, then a URL found in the text.
  static func extract(from items: [NSExtensionItem]) async throws -> SharePayload {
    let providers = items.flatMap { $0.attachments ?? [] }
    let pageTitle = items.lazy.compactMap { $0.attributedContentText?.string }
      .first { !$0.isEmpty }
    let text = await firstText(in: providers)

    for type in [UTType.pdf, .image] {
      guard let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(type.identifier) })
      else { continue }
      let copy = try await copyFile(from: provider, type: type)
      let mime =
        UTType(filenameExtension: copy.pathExtension)?.preferredMIMEType
        ?? type.preferredMIMEType ?? "application/octet-stream"
      return .file(url: copy, name: copy.lastPathComponent, mimeType: mime, note: ShareText.fileNote(from: text))
    }

    if let url = await firstWebURL(in: providers) ?? text.flatMap(ShareText.firstHTTPURL) {
      return .link(url: url, note: ShareText.linkNote(from: text, url: url), title: pageTitle)
    }
    throw ShareError.nothingToSave
  }

  /// The system deletes the provider's file when the handler returns, so copy it
  /// into the App Group first. It must outlive the sheet if it goes to the queue.
  private static func copyFile(from provider: NSItemProvider, type: UTType) async throws -> URL {
    let folder = AppGroup.directory("Inbox").appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    return try await withCheckedThrowingContinuation { continuation in
      _ = provider.loadFileRepresentation(for: type, openInPlace: false) { @Sendable url, _, error in
        guard let url else {
          continuation.resume(throwing: error ?? ShareError.nothingToSave)
          return
        }
        let dest = folder.appending(path: url.lastPathComponent)
        do {
          try FileManager.default.copyItem(at: url, to: dest)
          continuation.resume(returning: dest)
        } catch {
          continuation.resume(throwing: error)
        }
      }
    }
  }

  private static func firstWebURL(in providers: [NSItemProvider]) async -> URL? {
    for provider in providers where provider.canLoadObject(ofClass: URL.self) {
      let url: URL? = await withCheckedContinuation { continuation in
        _ = provider.loadObject(ofClass: URL.self) { @Sendable url, _ in continuation.resume(returning: url) }
      }
      if let url, ["http", "https"].contains(url.scheme?.lowercased()) { return url }
    }
    return nil
  }

  private static func firstText(in providers: [NSItemProvider]) async -> String? {
    for provider in providers where provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
      let text: String? = await withCheckedContinuation { continuation in
        _ = provider.loadObject(ofClass: String.self) { @Sendable text, _ in continuation.resume(returning: text) }
      }
      if let text, !text.isEmpty { return text }
    }
    return nil
  }
}
