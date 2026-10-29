import SwiftUI

/// reading → sending → saved, or queued when offline, or failed with the reason.
@Observable
final class ShareModel {
  enum Phase: Equatable {
    case reading
    case sending
    case saved
    case queued
    case failed(String)
  }

  var phase: Phase = .reading
  var title = "Memory Dump"
  var subtitle = ""

  private let items: [NSExtensionItem]
  private let close: () -> Void
  private var task: Task<Void, Never>?

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
      phase = .sending
      do {
        try await send(payload)
        phase = .saved
        await finish(after: .seconds(1.2))
      } catch is CancellationError {
        discard(payload)
      } catch let error as URLError where error.code == .cancelled {
        discard(payload)
      } catch is URLError {
        keep(payload)
      } catch APIError.signedOut, APIError.notConfigured {
        discard(payload)
        phase = .failed("Open Memory Dump and sign in first.")
      } catch APIError.http(let status, let detail) {
        discard(payload)
        phase = .failed(detail.isEmpty ? "HTTP \(status)" : "\(status) · \(detail)")
      } catch {
        discard(payload)
        phase = .failed(error.localizedDescription)
      }
    }
  }

  func cancel() {
    task?.cancel()
    close()
  }

  private func send(_ payload: SharePayload) async throws {
    switch payload {
    case .link(let url, let note, _):
      _ = try await APIClient.shared.ingest(url, note: note)
    case .file(let url, let name, let mimeType, let note):
      _ = try await APIClient.shared.ingestFile(at: url, name: name, mimeType: mimeType, note: note)
      discard(payload)
    }
  }

  /// No network: keep the share in the App Group. The app sends it later.
  private func keep(_ payload: SharePayload) {
    do {
      switch payload {
      case .link(let url, let note, _):
        try ShareQueue.enqueueLink(url, note: note)
      case .file(let url, let name, let mimeType, let note):
        try ShareQueue.enqueueFile(at: url, name: name, mimeType: mimeType, note: note)
      }
      phase = .queued
      Task { await finish(after: .seconds(1.8)) }
    } catch {
      discard(payload)
      phase = .failed("You’re offline, and the share couldn’t be kept.")
    }
  }

  /// Deletes the copied file once nothing needs it.
  private func discard(_ payload: SharePayload) {
    if case .file(let url, _, _, _) = payload {
      try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
    }
  }

  private func describe(_ payload: SharePayload) {
    switch payload {
    case .link(let url, _, let pageTitle):
      let host = url.host()?.replacing(/^www\./, with: "") ?? url.absoluteString
      title = pageTitle ?? host
      subtitle = pageTitle == nil ? "Link" : host
    case .file(_, let name, let mimeType, _):
      title = name
      subtitle = mimeType == "application/pdf" ? "PDF" : "Image"
    }
  }

  private func finish(after delay: Duration) async {
    try? await Task.sleep(for: delay)
    close()
  }
}

struct ShareView: View {
  let model: ShareModel

  var body: some View {
    VStack {
      Spacer()
      card
        .padding(.horizontal, 12)
        .padding(.bottom, 12)
    }
    .background(Color.black.opacity(0.25).ignoresSafeArea())
    .sensoryFeedback(trigger: model.phase) { _, phase in
      switch phase {
      case .saved, .queued: .success
      case .failed: .error
      default: nil
      }
    }
  }

  private var card: some View {
    VStack(spacing: 0) {
      HStack(spacing: 8) {
        Text("M")
          .font(.custom("Georgia", size: 13).weight(.semibold))
          .foregroundStyle(.white)
          .frame(width: 22, height: 22)
          .background(Color(red: 0.04, green: 0.04, blue: 0.05), in: .rect(cornerRadius: 6))
        Text("Memory Dump").font(.headline)
        Spacer()
      }
      .padding(.horizontal, 16)
      .padding(.vertical, 12)
      Divider()
      VStack(alignment: .leading, spacing: 2) {
        Text(model.title).font(.body.weight(.semibold)).lineLimit(2)
        if !model.subtitle.isEmpty {
          Text(model.subtitle).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(.horizontal, 16)
      .padding(.vertical, 12)
      Divider()
      status
        .font(.subheadline)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
    .background(.background, in: .rect(cornerRadius: 22))
    .shadow(color: .black.opacity(0.16), radius: 24, y: 8)
  }

  @ViewBuilder private var status: some View {
    HStack(spacing: 8) {
      switch model.phase {
      case .reading:
        ProgressView()
        Text("Reading the share…").foregroundStyle(.secondary)
        Spacer()
        Button("Cancel", action: model.cancel)
      case .sending:
        ProgressView()
        Text("Saving…").foregroundStyle(.secondary)
        Spacer()
        Button("Cancel", action: model.cancel)
      case .saved:
        Label("Saved", systemImage: "checkmark.circle.fill").foregroundStyle(.green).bold()
        Text("Analyzing in the background.").foregroundStyle(.secondary)
        Spacer()
      case .queued:
        Label("Kept", systemImage: "clock.fill").foregroundStyle(.orange).bold()
        Text("Offline. It sends when you open Memory Dump.").foregroundStyle(.secondary)
        Spacer()
      case .failed(let reason):
        Label("Not saved", systemImage: "exclamationmark.circle.fill").foregroundStyle(.red).bold()
        Text(reason).foregroundStyle(.secondary).lineLimit(3)
        Spacer()
        Button("Close", action: model.cancel)
      }
    }
  }
}
