import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import 'crypto_box.dart';

Map<String, dynamic> parseSetupCode(String raw) {
  if (raw.length > 12000 || !raw.startsWith('izbrainoe-setup:')) {
    throw const FormatException('Это не код настройки Изbrainого.');
  }
  final value = Map<String, dynamic>.from(
    jsonDecode(
      utf8.decode(
        base64Url.decode(
          base64Url.normalize(raw.substring('izbrainoe-setup:'.length)),
        ),
      ),
    ) as Map,
  );
  if (value['v'] == 1 &&
      value['mode'] == 'settings_only' &&
      value['provod_key'] is String) {
    final limit = value['limit_rub'];
    if (limit != null && (limit is! num || !limit.isFinite || limit < 0)) {
      throw const FormatException('Неверный лимит в коде настройки.');
    }
    if (value['model'] != null && value['model'] is! String) {
      throw const FormatException('Неверная модель в коде настройки.');
    }
    return {
      'v': 1,
      'mode': 'settings_only',
      'provod_key': value['provod_key'],
      'model': value['model'] ?? 'gemini-3.8-flash',
      'limit_rub': limit ?? 1000,
    };
  }
  if (value['v'] != 1 ||
      value['archive_code'] is! String ||
      value['provod_key'] is! String) {
    throw const FormatException('Неверный код настройки.');
  }
  return value;
}

Future<File?> downloadSetupArchive(
  Map<String, dynamic> setup,
  Directory temporary, {
  void Function(int received, int total)? onProgress,
}) async {
  if (setup['bundle_url'] == null) return null;
  final url = Uri.parse(setup['bundle_url'] as String),
      pin = setup['certificate_sha256'] as String? ?? '';
  final ip = InternetAddress.tryParse(url.host);
  final parts = url.host.split('.').map(int.tryParse).toList();
  final local =
      ip != null &&
      (ip.isLoopback ||
          (parts.length == 4 &&
              (parts[0] == 10 ||
                  (parts[0] == 192 && parts[1] == 168) ||
                  (parts[0] == 172 &&
                      (parts[1] ?? 0) >= 16 &&
                      (parts[1] ?? 0) <= 31))));
  if (url.scheme != 'https' ||
      !local ||
      url.userInfo.isNotEmpty ||
      !validDigest(pin) ||
      !RegExp(r'^/archive/[a-f0-9]{64}\.izbrainoe$').hasMatch(url.path)) {
    throw const FormatException('Неверный адрес переноса с Mac.');
  }
  await temporary.create(recursive: true);
  final output = File(p.join(temporary.path, 'setup-${newId()}.izbrainoe'));
  final client = HttpClient()
    ..connectionTimeout = const Duration(seconds: 10)
    ..badCertificateCallback = (certificate, host, port) =>
        host == url.host && port == url.port && digest(certificate.der) == pin;
  try {
    final request = await client
        .getUrl(url)
        .timeout(const Duration(seconds: 15));
    request.followRedirects = false;
    final response = await request.close().timeout(const Duration(seconds: 20));
    if (response.certificate == null ||
        digest(response.certificate!.der) != pin) {
      throw const FormatException(
        'Не совпал сертификат Mac. Перенос остановлен.',
      );
    }
    if (response.statusCode != 200) {
      throw const FormatException(
        'Ссылка переноса устарела. Открой настройку Android на Mac заново.',
      );
    }
    if (response.contentLength > 4 * 1024 * 1024 * 1024) {
      throw const FormatException('Архив слишком большой для переноса.');
    }
    final writer = output.openWrite();
    var count = 0;
    onProgress?.call(0, response.contentLength);
    try {
      await for (final block in response.timeout(const Duration(seconds: 30))) {
        count += block.length;
        if (count > 4 * 1024 * 1024 * 1024) {
          throw const FormatException('Слишком большой архив.');
        }
        writer.add(block);
        onProgress?.call(count, response.contentLength);
      }
      await writer.flush();
    } finally {
      await writer.close();
    }
    return output;
  } catch (error) {
    if (await output.exists()) await output.delete();
    if (error is SocketException || error is TimeoutException) {
      throw const FormatException(
        "Телефон не смог получить архив с Mac. Подключи оба устройства к одной Wi-Fi сети, временно отключи VPN и заново открой «Перенести с Mac.command». Затем повтори перенос.",
      );
    }
    rethrow;
  } finally {
    client.close(force: true);
  }
}
