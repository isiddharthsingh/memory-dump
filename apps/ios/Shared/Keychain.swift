import Foundation
import Security

/// Generic-password items in the shared Keychain group, so the share extension
/// reads the session the app wrote.
enum Keychain {
  private static let service = "com.siddharthsingh.memorydump"

  static func data(_ key: String) -> Data? {
    var out: AnyObject?
    let status = withGroup { group in
      var q = query(key, group: group)
      q[kSecReturnData as String] = true
      q[kSecMatchLimit as String] = kSecMatchLimitOne
      return SecItemCopyMatching(q as CFDictionary, &out)
    }
    return status == errSecSuccess ? out as? Data : nil
  }

  static func string(_ key: String) -> String? {
    data(key).flatMap { String(data: $0, encoding: .utf8) }
  }

  static func set(_ data: Data, for key: String) {
    let attrs: [String: Any] = [
      kSecValueData as String: data,
      kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
    ]
    withGroup { group in
      let q = query(key, group: group)
      let status = SecItemUpdate(q as CFDictionary, attrs as CFDictionary)
      guard status == errSecItemNotFound else { return status }
      return SecItemAdd(q.merging(attrs) { $1 } as CFDictionary, nil)
    }
  }

  static func set(_ string: String, for key: String) {
    set(Data(string.utf8), for: key)
  }

  static func delete(_ key: String) {
    withGroup { group in SecItemDelete(query(key, group: group) as CFDictionary) }
  }

  private static func query(_ key: String, group: String?) -> [String: Any] {
    var q: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: key,
    ]
    if let group { q[kSecAttrAccessGroup as String] = group }
    return q
  }

  /// Uses the shared group, and falls back to the app's own Keychain when the
  /// build has no Keychain group entitlement (an unsigned simulator build).
  @discardableResult
  private static func withGroup(_ op: (String?) -> OSStatus) -> OSStatus {
    let status = op(AppGroup.keychainAccessGroup)
    return status == errSecMissingEntitlement ? op(nil) : status
  }
}
