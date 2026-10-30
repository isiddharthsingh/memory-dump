import SwiftUI

/// reading → sending → saved, or queued when offline, or failed with the reason.
@Observable
final class ShareModel {
  enum Phase: Equatable {
    case reading
    case sending
    case saved
    /// Kept for the app to send, with why.
    case queued(String)
    case failed(String)
  }

  var phase: Phase = .reading
  var title = ""
  /// Set once the extension is on screen; cleared to slide the pill away.
  var shown = false

  private let items: [NSExtensionItem]
  private let close: () -> Void
  private var task: Task<Void, Never>?
  private var closing = false

  init(items: [NSExtensionItem], close: @escaping () -> Void) {
    self.items = items
    self.close = close
  }

  func start() {
    task = Task {
      let payload: SharePayload
      do {
        payload = try await SharePayload.extract(from: items)
      } catch {
        phase = .failed(error.localizedDescription)
        return
      }
      describe(payload)
      // Queued before it is sent, so nothing is lost if the server never
      // answers or the extension closes first. The app sends what's left.
      let entry: QueuedShare
      do {
        entry = try enqueue(payload)
      } catch {
        phase = .failed("This share couldn’t be kept: \(error.localizedDescription)")
        return
      }
      phase = .sending
      switch await ShareQueue.send(entry) {
      case .sent:
        phase = .saved
        await finish(after: .seconds(1.2))
      case .waiting(let error):
        if error is CancellationError || (error as? URLError)?.code == .cancelled { return }
        let reason =
          switch error {
          case APIError.signedOut: "Sign in to Memory Dump and it sends."
          // It sends once the app is updated.
          case APIError.updateRequired: error.localizedDescription
          case is ShareQueue.InProgress: "Memory Dump is sending it."
          default: "Your server didn’t answer. It sends when it’s back."
          }
        phase = .queued(reason)
        await finish(after: .seconds(1.8))
      case .refused(let error):
        if case APIError.http(let status, let detail, _) = error {
          phase = .failed(detail.isEmpty ? "HTTP \(status)" : "\(status) · \(detail)")
        } else {
          phase = .failed(error.localizedDescription)
        }
      }
    }
  }

  /// Closes the pill. A share still sending stays queued for the app.
  func cancel() {
    task?.cancel()
    Task { await finish() }
  }

  private func enqueue(_ payload: SharePayload) throws -> QueuedShare {
    switch payload {
    case .link(let url, let note, _):
      return try ShareQueue.enqueueLink(url, note: note, title: title)
    case .file(let url, let name, let mimeType, let note):
      defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
      return try ShareQueue.enqueueFile(at: url, name: name, mimeType: mimeType, note: note)
    }
  }

  private func describe(_ payload: SharePayload) {
    switch payload {
    case .link(let url, _, let pageTitle):
      title = pageTitle ?? url.host()?.replacing(/^www\./, with: "") ?? url.absoluteString
    case .file(_, let name, _, _):
      title = name
    }
  }

  private func finish(after delay: Duration = .zero) async {
    try? await Task.sleep(for: delay)
    // Cancel can race a save that just finished; complete the request once.
    guard !closing else { return }
    closing = true
    shown = false
    try? await Task.sleep(for: .milliseconds(300))  // let the pill slide out
    close()
  }
}

/// A small status pill under the Dynamic Island or notch. The extension is shown
/// full screen over the app with a clear background (see Info.plist), so the page
/// stays visible around it.
struct ShareView: View {
  let model: ShareModel

  private static let ink = Color(red: 0.04, green: 0.04, blue: 0.05)

  var body: some View {
    ZStack(alignment: .top) {
      // Taps outside the pill dismiss a failure; while saving they do nothing.
      Color.clear
        .contentShape(.rect)
        .ignoresSafeArea()
        .onTapGesture { if isFailed { model.cancel() } }
      if model.shown {
        pill
          .padding(.horizontal, 16)
          .padding(.top, 6)
          .transition(.move(edge: .top).combined(with: .opacity))
      }
    }
    .animation(.spring(duration: 0.4, bounce: 0.25), value: model.shown)
    .animation(.snappy, value: model.phase)
    .sensoryFeedback(trigger: model.phase) { _, phase in
      switch phase {
      case .saved, .queued: .success
      case .failed: .error
      default: nil
      }
    }
  }

  private var pill: some View {
    HStack(spacing: 12) {
      glyph.frame(width: 28, height: 28)
      VStack(alignment: .leading, spacing: 1) {
        Text(headline).font(.subheadline.weight(.semibold))
        Text(detail)
          .font(.footnote)
          .foregroundStyle(.white.opacity(0.7))
          .lineLimit(isFailed ? 3 : 2)
      }
      if isBusy || isFailed {
        Button(isBusy ? "Cancel" : "Close", systemImage: "xmark", action: model.cancel)
          .labelStyle(.iconOnly)
          .font(.footnote.weight(.bold))
          .foregroundStyle(.white.opacity(0.8))
          .frame(width: 28, height: 28)
          .background(.white.opacity(0.15), in: .circle)
      }
    }
    .foregroundStyle(.white)
    .padding(.leading, 12)
    .padding(.trailing, 14)
    .padding(.vertical, 10)
    .background(Self.ink, in: .rect(cornerRadius: 26))
    .shadow(color: .black.opacity(0.2), radius: 16, y: 6)
  }

  @ViewBuilder private var glyph: some View {
    switch model.phase {
    case .reading, .sending:
      ProgressView().tint(.white)
    case .saved:
      Image(systemName: "checkmark.circle.fill").font(.title2).foregroundStyle(.green)
    case .queued:
      Image(systemName: "clock.fill").font(.title2).foregroundStyle(.orange)
    case .failed:
      Image(systemName: "exclamationmark.circle.fill").font(.title2).foregroundStyle(.red)
    }
  }

  private var headline: String {
    switch model.phase {
    case .reading, .sending: "Saving to Memory Dump"
    case .saved: "Saved to Memory Dump"
    case .queued: "Saved for later"
    case .failed: "Not saved"
    }
  }

  private var detail: String {
    switch model.phase {
    case .reading: "Reading the share…"
    case .sending, .saved: model.title
    case .queued(let reason): reason
    case .failed(let reason): reason
    }
  }

  private var isBusy: Bool { model.phase == .reading || model.phase == .sending }

  private var isFailed: Bool {
    if case .failed = model.phase { true } else { false }
  }
}
