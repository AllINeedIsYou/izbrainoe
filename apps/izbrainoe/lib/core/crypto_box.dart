import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as hashes;
import 'package:cryptography/cryptography.dart';

Uint8List randomBytes(int count) {
  final random = Random.secure();
  return Uint8List.fromList(List.generate(count, (_) => random.nextInt(256)));
}

String newId() =>
    randomBytes(16).map((b) => b.toRadixString(16).padLeft(2, '0')).join();
String digest(List<int> bytes) => hashes.sha256.convert(bytes).toString();
bool validId(String value) => RegExp(r'^[a-f0-9]{32}$').hasMatch(value);
bool validDigest(String value) => RegExp(r'^[a-f0-9]{64}$').hasMatch(value);

class ArchiveCipher {
  final SecretKey key;
  final String archiveId;
  final _algorithm = AesGcm.with256bits();
  ArchiveCipher._(this.key, this.archiveId);
  static Future<ArchiveCipher> fromCode(String code) async {
    final bytes = base64Url.decode(base64Url.normalize(code.trim()));
    if (bytes.length != 32) throw const FormatException('Неверный код архива.');
    return ArchiveCipher._(SecretKey(bytes), digest(bytes).substring(0, 32));
  }

  static String createCode() =>
      base64UrlEncode(randomBytes(32)).replaceAll('=', '');
  Future<Uint8List> seal(List<int> bytes, String purpose) async {
    final box = await _algorithm.encrypt(
      bytes,
      secretKey: key,
      nonce: randomBytes(12),
      aad: utf8.encode('izbrainoe-v1:$archiveId:$purpose'),
    );
    return Uint8List.fromList([
      1,
      ...box.nonce,
      ...box.mac.bytes,
      ...box.cipherText,
    ]);
  }

  Future<Uint8List> open(List<int> bytes, String purpose) async {
    if (bytes.length < 29 || bytes.first != 1) {
      throw const FormatException('Неизвестный формат шифрования.');
    }
    final box = SecretBox(
      bytes.sublist(29),
      nonce: bytes.sublist(1, 13),
      mac: Mac(bytes.sublist(13, 29)),
    );
    return Uint8List.fromList(
      await _algorithm.decrypt(
        box,
        secretKey: key,
        aad: utf8.encode('izbrainoe-v1:$archiveId:$purpose'),
      ),
    );
  }

  Future<Map<String, dynamic>> openJson(
    List<int> bytes,
    String purpose,
  ) async => Map<String, dynamic>.from(
    jsonDecode(utf8.decode(await open(bytes, purpose))) as Map,
  );
  Future<Uint8List> sealJson(Map<String, dynamic> value, String purpose) =>
      seal(utf8.encode(jsonEncode(value)), purpose);
}

/// A sync key cannot unlock credentials: they have a separate password envelope.
class PasswordEnvelope {
  static Future<SecretKey> _derive(String password, List<int> salt) => Pbkdf2(
    macAlgorithm: Hmac.sha256(),
    iterations: 600000,
    bits: 256,
  ).deriveKey(secretKey: SecretKey(utf8.encode(password)), nonce: salt);
  static Future<Map<String, dynamic>> encrypt(
    Map<String, dynamic> value,
    String password,
  ) async {
    if (password.runes.length < 8) {
      throw const FormatException('Главный пароль — минимум 8 символов.');
    }
    final salt = randomBytes(16);
    final box = await AesGcm.with256bits().encrypt(
      utf8.encode(jsonEncode(value)),
      secretKey: await _derive(password, salt),
      aad: utf8.encode('izbrainoe-vault-v1'),
    );
    return {
      'v': 1,
      'salt': base64Encode(salt),
      'nonce': base64Encode(box.nonce),
      'mac': base64Encode(box.mac.bytes),
      'data': base64Encode(box.cipherText),
    };
  }

  static Future<Map<String, dynamic>> decrypt(
    Map<String, dynamic> value,
    String password,
  ) async {
    if (value['v'] != 1) {
      throw const FormatException('Неизвестная версия хранилища.');
    }
    if (value['nonce'] == null && value['ciphertext'] is String) {
      if (value['kdf'] != 'PBKDF2-SHA256' || value['iterations'] != 600000) {
        throw const FormatException('Неизвестный формат старого хранилища.');
      }
      final combined = base64Decode(value['ciphertext'] as String);
      if (combined.length < 16) {
        throw const FormatException('Повреждено старое хранилище.');
      }
      value = {
        'v': 1,
        'salt': value['salt'],
        'nonce': value['iv'],
        'data': base64Encode(combined.sublist(0, combined.length - 16)),
        'mac': base64Encode(combined.sublist(combined.length - 16)),
      };
    }
    final salt = base64Decode(value['salt'] as String),
        nonce = base64Decode(value['nonce'] as String);
    if (salt.length != 16 || nonce.length != 12) {
      throw const FormatException('Повреждено хранилище.');
    }
    final clear = await AesGcm.with256bits().decrypt(
      SecretBox(
        base64Decode(value['data'] as String),
        nonce: nonce,
        mac: Mac(base64Decode(value['mac'] as String)),
      ),
      secretKey: await _derive(password, salt),
      aad: utf8.encode('izbrainoe-vault-v1'),
    );
    return Map<String, dynamic>.from(jsonDecode(utf8.decode(clear)) as Map);
  }
}
