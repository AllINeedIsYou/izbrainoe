import Foundation
import Security
import CryptoKit
import CommonCrypto

enum VaultError: Error { case invalid, locked, wrongPassword, keychain }
let service = "local.izbrainoe.telegram-vault"

func baseQuery(_ account: String) -> [String: Any] {
    return [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: account]
}

func stored(_ account: String) throws -> [String: String] {
    var query = baseQuery(account)
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
          let data = result as? Data,
          let value = try JSONSerialization.jsonObject(with: data) as? [String: String] else { throw VaultError.locked }
    return value
}

func open(_ envelope: [String: Any], key: Data) throws -> [[String: Any]] {
    guard envelope["v"] as? Int == 1, envelope["iterations"] as? Int == 600000,
          envelope["kdf"] as? String == "PBKDF2-SHA256",
          let iv = Data(base64Encoded: envelope["iv"] as? String ?? ""), iv.count == 12,
          let ciphertext = Data(base64Encoded: envelope["ciphertext"] as? String ?? ""),
          ciphertext.count >= 16, ciphertext.count <= 2000000 else { throw VaultError.invalid }
    let sealed = try AES.GCM.SealedBox(combined: iv + ciphertext)
    var plain = try AES.GCM.open(sealed, using: SymmetricKey(data: key), authenticating: Data("izbrainoe-vault-v1".utf8))
    defer { plain.resetBytes(in: 0..<plain.count) }
    guard let value = try JSONSerialization.jsonObject(with: plain) as? [String: Any],
          value["check"] as? String == "izbrainoe-v1",
          let entries = value["entries"] as? [[String: Any]] else { throw VaultError.invalid }
    return entries
}

func handle(_ request: [String: Any]) throws -> [String: Any] {
    guard let account = request["account"] as? String, !account.isEmpty,
          let operation = request["operation"] as? String else { throw VaultError.invalid }
    if operation == "disable" {
        let status = SecItemDelete(baseQuery(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw VaultError.keychain }
        return ["ok": true]
    }
    guard let envelope = request["envelope"] as? [String: Any],
          let saltText = envelope["salt"] as? String, let salt = Data(base64Encoded: saltText), salt.count == 16 else { throw VaultError.invalid }
    if operation == "enable" {
        guard let password = request["password"] as? String, password.utf16.count >= 8 else { throw VaultError.invalid }
        var key = Data(count: 32)
        let code = key.withUnsafeMutableBytes { output in
            salt.withUnsafeBytes { saltBytes in
                password.withCString { passwordBytes in
                    CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), passwordBytes, password.utf8.count,
                                        saltBytes.bindMemory(to: UInt8.self).baseAddress!, salt.count,
                                        CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256), 600000,
                                        output.bindMemory(to: UInt8.self).baseAddress!, 32)
                }
            }
        }
        defer { key.resetBytes(in: 0..<key.count) }
        guard code == kCCSuccess else { throw VaultError.invalid }
        do { _ = try open(envelope, key: key) } catch { throw VaultError.wrongPassword }
        let data = try JSONSerialization.data(withJSONObject: ["key": key.base64EncodedString(), "salt": saltText])
        let attributes = [kSecValueData as String: data]
        var status = SecItemUpdate(baseQuery(account) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var query = baseQuery(account)
            query[kSecValueData as String] = data
            query[kSecAttrLabel as String] = "Изbrainое: выдача паролей в Telegram"
            status = SecItemAdd(query as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw VaultError.keychain }
        return ["ok": true]
    }
    let value = try stored(account)
    guard value["salt"] == saltText, var key = Data(base64Encoded: value["key"] ?? ""), key.count == 32 else { throw VaultError.locked }
    defer { key.resetBytes(in: 0..<key.count) }
    if operation == "status" { return ["ok": true, "enabled": true] }
    guard operation == "read" else { throw VaultError.invalid }
    return ["ok": true, "entries": try open(envelope, key: key)]
}

do {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard data.count <= 2100000, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw VaultError.invalid }
    let response = try handle(request)
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: response))
} catch {
    let code = (error as? VaultError) == .wrongPassword ? "wrong_password" : (error as? VaultError) == .locked ? "locked" : "unavailable"
    let response: [String: Any] = ["ok": false, "code": code]
    FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: response))
}
