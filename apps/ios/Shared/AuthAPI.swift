import Foundation

/// SuperTokens' sign-in, sign-up and refresh routes, with header-based
/// sessions: requests send `st-auth-mode: header`, tokens come back in the
/// `st-access-token` and `st-refresh-token` response headers.
enum AuthAPI {
  static func signIn(email: String, password: String) async throws -> Session {
    try await session("/auth/signin", [("email", email), ("password", password)])
  }

  static func signUp(email: String, password: String, displayName: String?) async throws -> Session {
    var fields = [("email", email), ("password", password)]
    if let displayName { fields.append(("name", displayName)) }
    return try await session("/auth/signup", fields)
  }

  /// New tokens for old. SuperTokens rotates the refresh token each time, and a
  /// refresh token used again after its replacement ends the session.
  static func refresh(session: Session) async throws -> Session {
    var req = try APIClient.shared.request(
      "/auth/session/refresh", method: "POST", token: session.refreshToken)
    req.setValue("header", forHTTPHeaderField: "st-auth-mode")
    let (data, resp) = try await APIClient.session.data(for: req)
    let http = resp as? HTTPURLResponse
    guard let http, http.statusCode == 200, let tokens = Tokens(http) else {
      throw APIError(status: http?.statusCode ?? 0, body: data, response: http)
    }
    return Session(accessToken: tokens.access, refreshToken: tokens.refresh, user: session.user)
  }

  private struct Tokens {
    let access: String
    let refresh: String

    init?(_ response: HTTPURLResponse) {
      guard let access = response.value(forHTTPHeaderField: "st-access-token"), !access.isEmpty,
        let refresh = response.value(forHTTPHeaderField: "st-refresh-token"), !refresh.isEmpty
      else { return nil }
      self.access = access
      self.refresh = refresh
    }
  }

  private struct Form: Encodable {
    struct Field: Encodable {
      let id: String
      let value: String
    }
    let formFields: [Field]
  }

  /// Signs in or up, then loads the profile with the new access token. Too
  /// many tries from one address get a 429 whose detail says so.
  private static func session(_ path: String, _ fields: [(String, String)]) async throws -> Session {
    let form = Form(formFields: fields.map { .init(id: $0, value: $1) })
    var req = try APIClient.shared.request(path, method: "POST", body: form, token: nil)
    req.setValue("header", forHTTPHeaderField: "st-auth-mode")
    let (data, resp) = try await APIClient.session.data(for: req)
    let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard status == 200 else { throw APIError(status: status, body: data, response: resp as? HTTPURLResponse) }
    if let problem = try JSONDecoder().decode(AuthResult.self, from: data).problem {
      throw APIError.auth(problem)
    }
    guard let http = resp as? HTTPURLResponse, let tokens = Tokens(http) else {
      throw APIError.auth("The server didn’t start a session.")
    }
    let me = try APIClient.shared.request("/auth/me", token: tokens.access)
    let (profile, meResp) = try await APIClient.session.data(for: me)
    let meStatus = (meResp as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(meStatus) else {
      throw APIError(status: meStatus, body: profile, response: meResp as? HTTPURLResponse)
    }
    let user = try JSONDecoder.api.decode(User.self, from: profile)
    return Session(accessToken: tokens.access, refreshToken: tokens.refresh, user: user)
  }
}
