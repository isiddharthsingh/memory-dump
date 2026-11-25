import Foundation

enum APIError: LocalizedError, Equatable {
  case signedOut
  /// The server answered with a non-2xx status. The text is FastAPI's `detail`;
  /// `retryAfter` is the Retry-After header's seconds, sent with a 429 or 503.
  case http(Int, String, retryAfter: TimeInterval? = nil)
  /// Sign-in or sign-up was refused; the text says why, ready to show.
  case auth(String)
  /// `APIClient.saved` has no copy of this.
  case notSaved
  /// 426: the server no longer takes this build. The text says to update.
  case updateRequired(String)

  /// The error for a non-2xx answer.
  init(status: Int, body: Data, response: HTTPURLResponse?) {
    let detail = FastAPIError.detail(in: body)
    if status == 426 {
      self = .updateRequired(detail)
    } else {
      self = .http(
        status, detail,
        retryAfter: Self.seconds(retryAfter: response?.value(forHTTPHeaderField: "Retry-After")))
    }
  }

  var errorDescription: String? {
    switch self {
    case .signedOut: "Not signed in."
    case .http(429, "", _): "Too many tries. Try again later."
    // No sentence to show: a proxy's page, or a bare status.
    case .http(500..., "", _): "Memory Dump isn’t available right now. Try again in a moment."
    case .http(_, "", _): "Something went wrong. Try again."
    case .http(_, let detail, _): detail
    case .auth(let message): message
    case .notSaved: "Not saved on this phone yet."
    case .updateRequired(let detail):
      detail.isEmpty ? "This version of Memory Dump is no longer supported. Update the app." : detail
    }
  }

  /// Retry-After as the server sends it: whole seconds. Nil for anything else.
  static func seconds(retryAfter header: String?) -> TimeInterval? {
    guard let header, let seconds = TimeInterval(header.trimmingCharacters(in: .whitespaces)),
      seconds.isFinite, seconds >= 0
    else { return nil }
    return seconds
  }
}

extension Error {
  /// The request never got an answer: no network, a wrong address, or the
  /// server (or the proxy in front of it) is down.
  var isUnreachable: Bool {
    if let error = self as? URLError { return error.code != .cancelled }
    if case .http(let status, _, _)? = self as? APIError { return [502, 503, 504].contains(status) }
    return false
  }

  /// How long the server asked to wait before trying again.
  var retryAfter: TimeInterval? {
    if case .http(_, _, let seconds)? = self as? APIError { return seconds }
    return nil
  }

  /// A page's `before` row is gone (deleted since the page before it was
  /// read), so paging has to start again from the top.
  var isUnknownCursor: Bool {
    if case .http(404, "Unknown cursor", _)? = self as? APIError { return true }
    return false
  }
}

/// FastAPI errors look like {"detail": "..."}; validation errors carry a list.
/// SuperTokens' own /auth routes answer {"message": "..."}. Anything else (a
/// proxy's HTML page, a plain-text 500) is not written for people, so it reads
/// as no detail and APIError says something general. Nonisolated: APIError,
/// an Error and so nonisolated, reads it.
nonisolated enum FastAPIError {
  static func detail(in data: Data) -> String {
    guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
      return ""
    }
    if let s = obj["detail"] as? String { return s }
    if let list = obj["detail"] as? [[String: Any]], let msg = list.first?["msg"] as? String {
      return msg
    }
    return obj["message"] as? String ?? ""
  }
}
