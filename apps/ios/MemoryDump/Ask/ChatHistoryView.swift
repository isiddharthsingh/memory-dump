import SwiftUI

/// Past Ask chats from the server, most recent first, in the drawer on the
/// left of Ask. Tap one to reopen it; swipe to delete.
struct ChatHistoryView: View {
  let model: AskModel
  let drawer: HistoryDrawer
  @Environment(\.historyShown) private var shown
  @State private var query = ""
  @State private var searching = false
  @State private var opening: UUID?
  @State private var openError: String?

  private func sections(_ history: [ChatSummary]) -> [(title: String, values: [ChatSummary])] {
    let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
    let matching = history.sorted { $0.updatedAt > $1.updatedAt }
      .filter { q.isEmpty || $0.questions.contains { $0.localizedCaseInsensitiveContains(q) } }
    return LibraryModel.grouped(matching, by: \.updatedAt)
  }

  var body: some View {
    NavigationStack {
      Group {
        if let history = model.history {
          if history.isEmpty {
            ContentUnavailableView {
              Label("No chats yet", systemImage: "bubble.left.and.text.bubble.right")
            } description: {
              Text("Your questions and their answers are kept here, on any device you sign in to.")
            }
          } else {
            list(history)
          }
        } else if let error = model.historyError {
          ErrorNotice(title: "Couldn’t load your chats", detail: error.localizedDescription) {
            Task { await model.loadHistory() }
          }
          .padding(20)
          .frame(maxHeight: .infinity, alignment: .top)
        } else {
          ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        }
      }
      .navigationTitle("History")
      .navigationBarTitleDisplayMode(.inline)
      .alert(
        "Couldn’t open this chat",
        isPresented: Binding(get: { openError != nil }, set: { if !$0 { openError = nil } })
      ) {
        Button("OK") {}
      } message: {
        Text(openError ?? "")
      }
    }
    // Fetched as it starts to show, so a swipe already has the list; closing
    // puts the search keyboard away.
    .onChange(of: shown) {
      if shown { Task { await model.loadHistory() } } else { searching = false }
    }
  }

  private func list(_ history: [ChatSummary]) -> some View {
    let sections = sections(history)
    return List {
      ForEach(sections, id: \.title) { section in
        Section {
          ForEach(section.values) { chat in row(chat) }
        } header: {
          Text(section.title).font(.footnote.weight(.semibold)).foregroundStyle(Palette.ink2).textCase(nil)
        }
      }
    }
    .listStyle(.plain)
    .refreshable { await model.loadHistory() }
    .searchable(text: $query, isPresented: $searching, prompt: "Search chats")
    .overlay {
      if sections.isEmpty { ContentUnavailableView.search(text: query) }
    }
  }

  private func row(_ chat: ChatSummary) -> some View {
    let open = chat.id == model.chatId
    return Button {
      guard opening == nil else { return }
      Haptics.selection()
      opening = chat.id
      Task {
        do {
          try await model.open(chat.id)
          drawer.setOpen(false)
        } catch {
          Haptics.error()
          openError = error.localizedDescription
        }
        opening = nil
      }
    } label: {
      HStack(spacing: 12) {
        VStack(alignment: .leading, spacing: 4) {
          Text(chat.title).font(.headline).foregroundStyle(Palette.ink).lineLimit(2)
          Text("\(Format.plural(chat.questions.count, "question")) · \(Format.relativeTime(chat.updatedAt))")
            .font(.footnote).foregroundStyle(Palette.ink3)
        }
        Spacer(minLength: 0)
        if opening == chat.id {
          ProgressView()
        } else if open {
          Image(systemName: "checkmark").font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent)
        }
      }
      .contentShape(.rect)
    }
    .buttonStyle(.plain)
    .listRowInsets(EdgeInsets(top: 12, leading: 20, bottom: 12, trailing: 20))
    .swipeActions {
      Button("Delete", systemImage: "trash", role: .destructive) { delete(chat) }
    }
    .contextMenu {
      Button("Delete", systemImage: "trash", role: .destructive) { delete(chat) }
    }
    .accessibilityLabel("\(chat.title), \(Format.plural(chat.questions.count, "question"))\(open ? ", open" : "")")
  }

  private func delete(_ chat: ChatSummary) {
    Task { await model.delete(chat.id) }
  }
}
