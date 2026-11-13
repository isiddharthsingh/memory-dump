import Foundation

enum Multipart {
  /// Writes a multipart/form-data body to a temporary file: the file part,
  /// copied in 1 MB chunks, then an optional `share_text` field.
  static func write(file: URL, name: String, mimeType: String, note: String?, boundary: String)
    throws -> URL
  {
    let out = FileManager.default.temporaryDirectory.appending(path: "upload-\(UUID().uuidString)")
    FileManager.default.createFile(atPath: out.path(percentEncoded: false), contents: nil)
    let writer = try FileHandle(forWritingTo: out)
    defer { try? writer.close() }
    let safeName = name.replacing("\"", with: "")
    try writer.write(contentsOf: Data(
      "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: \(mimeType)\r\n\r\n"
        .utf8))
    let reader = try FileHandle(forReadingFrom: file)
    defer { try? reader.close() }
    while let chunk = try reader.read(upToCount: 1 << 20), !chunk.isEmpty {
      try writer.write(contentsOf: chunk)
    }
    if let note, !note.isEmpty {
      try writer.write(contentsOf: Data(
        "\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"share_text\"\r\n\r\n\(note)"
          .utf8))
    }
    try writer.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    return out
  }
}
