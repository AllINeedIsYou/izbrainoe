import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'archive_store.dart';
import 'crypto_box.dart';

abstract class RemoteStore {
  Future<void> ensure(String directory);
  Future<List<String>> list(String directory);
  Future<Uint8List?> get(String path);
  Future<void> put(String path, List<int> bytes, {bool replace = false});
  Future<void> delete(String path);
  void close() {}
}

class RemoteFailure implements Exception {
  final String message;
  final int? status;
  RemoteFailure(this.message, [this.status]);
  @override
  String toString() => message;
}

class YandexDisk extends RemoteStore {
  final String token, root;
  final http.Client _client;
  YandexDisk(this.token, String archiveId, {http.Client? client})
    : root = 'app:/izbrainoe-v1/$archiveId',
      _client = client ?? http.Client() {
    if (!validId(archiveId) || token.trim().isEmpty) {
      throw const FormatException('Нужен токен Яндекс Диска.');
    }
  }
  String _path(String path) {
    if (!RegExp(r'^[a-z0-9/._-]*$').hasMatch(path) ||
        path.contains('..') ||
        path.startsWith('/')) {
      throw const FormatException('Неверный путь синхронизации.');
    }
    return path.isEmpty ? root : '$root/$path';
  }

  Future<http.Response> _api(
    String endpoint,
    Map<String, String> query, {
    String method = 'GET',
  }) async {
    final request =
        http.Request(
            method,
            Uri.https(
              'cloud-api.yandex.net',
              '/v1/disk/resources$endpoint',
              query,
            ),
          )
          ..headers['Authorization'] = 'OAuth $token'
          ..followRedirects = false;
    try {
      final stream = await _client
          .send(request)
          .timeout(const Duration(seconds: 15));
      if ((stream.contentLength ?? 0) > 8 * 1024 * 1024) {
        throw RemoteFailure('Слишком большой ответ хранилища.');
      }
      return await http.Response.fromStream(stream)
          .timeout(const Duration(seconds: 20));
    } on SocketException {
      throw RemoteFailure('Нет соединения с Яндекс Диском.');
    }
  }

  Never _fail(int code) {
    if (code == 401 || code == 403) {
      throw RemoteFailure(
        'Яндекс Диск не разрешил доступ. Проверь подключение и права токена.',
        code,
      );
    }
    if (code == 507 || code == 413) {
      throw RemoteFailure(
        'На Яндекс Диске не хватает места. Файлы сохранены на устройстве.',
        code,
      );
    }
    if (code == 429) {
      throw RemoteFailure(
        'Яндекс Диск временно ограничил запросы. Повторим позже.',
        code,
      );
    }
    throw RemoteFailure(
      'Яндекс Диск недоступен ($code). Локальные данные сохранены.',
      code,
    );
  }

  @override
  Future<void> ensure(String directory) async {
    final full = _path(directory), parts = full.split('/');
    var path = parts.first;
    for (final segment in parts.skip(1)) {
      if (segment.isEmpty) continue;
      path = '$path/$segment';
      final r = await _api('', {'path': path}, method: 'PUT');
      if (r.statusCode != 201 && r.statusCode != 409) _fail(r.statusCode);
    }
  }

  @override
  Future<List<String>> list(String directory) async {
    final result = <String>[];
    var offset = 0;
    while (true) {
      final r = await _api('', {
        'path': _path(directory),
        'limit': '1000',
        'offset': '$offset',
      });
      if (r.statusCode == 404) return [];
      if (r.statusCode != 200) _fail(r.statusCode);
      final j = jsonDecode(r.body) as Map, embedded = j['_embedded'] as Map?;
      if (embedded == null) {
        throw RemoteFailure('Ожидалась папка синхронизации.');
      }
      final items = embedded['items'] as List;
      for (final item in items) {
        final name = (item as Map)['name'];
        if (name is String &&
            RegExp(r'^[a-z0-9._-]+$').hasMatch(name) &&
            !name.contains('..')) {
          result.add(name);
        }
      }
      offset += items.length;
      if (items.isEmpty || offset >= (embedded['total'] as num)) break;
      if (offset > 100000) {
        throw RemoteFailure('Слишком много объектов в папке синхронизации.');
      }
    }
    return result;
  }

  Uri _transfer(String href) {
    final u = Uri.parse(href), host = Uri.parse(href).host;
    if (u.scheme != 'https' ||
        u.userInfo.isNotEmpty ||
        (u.hasPort && u.port != 443) ||
        !(host.endsWith('.yandex.net') ||
            host.endsWith('.yandex.ru') ||
            host.endsWith('.yandexcloud.net'))) {
      throw RemoteFailure('Хранилище вернуло непроверенный адрес передачи.');
    }
    return u;
  }

  @override
  Future<Uint8List?> get(String path) async {
    final r = await _api('/download', {'path': _path(path)});
    if (r.statusCode == 404) return null;
    if (r.statusCode != 200) _fail(r.statusCode);
    final request = http.Request(
      'GET',
      _transfer((jsonDecode(r.body) as Map)['href'] as String),
    )..followRedirects = false;
    final response = await _client
        .send(request)
        .timeout(const Duration(seconds: 30));
    if (response.statusCode != 200) _fail(response.statusCode);
    if ((response.contentLength ?? 0) > 8 * 1024 * 1024) {
      throw RemoteFailure('Слишком большой фрагмент синхронизации.');
    }
    final bytes = BytesBuilder();
    await for (final block in response.stream.timeout(
      const Duration(seconds: 30),
    )) {
      bytes.add(block);
      if (bytes.length > 8 * 1024 * 1024) {
        throw RemoteFailure('Слишком большой фрагмент синхронизации.');
      }
    }
    return bytes.takeBytes();
  }

  @override
  Future<void> put(String path, List<int> bytes, {bool replace = false}) async {
    final r = await _api('/upload', {
      'path': _path(path),
      'overwrite': replace.toString(),
    });
    if (r.statusCode == 409 && !replace) return;
    if (r.statusCode != 200) _fail(r.statusCode);
    final request =
        http.Request(
            'PUT',
            _transfer((jsonDecode(r.body) as Map)['href'] as String),
          )
          ..bodyBytes = bytes
          ..followRedirects = false;
    // Signed transfer URLs never receive the OAuth header.
    final response = await _client
        .send(request)
        .timeout(const Duration(seconds: 45));
    await response.stream.drain<void>();
    if (response.statusCode != 201 &&
        response.statusCode != 200 &&
        response.statusCode != 202 &&
        !(response.statusCode == 409 && !replace)) {
      _fail(response.statusCode);
    }
  }

  @override
  Future<void> delete(String path) async {
    final r = await _api('', {
      'path': _path(path),
      'permanently': 'true',
    }, method: 'DELETE');
    if (![202, 204, 404].contains(r.statusCode)) _fail(r.statusCode);
  }

  @override
  void close() => _client.close();
}

class SyncReport {
  final int downloaded, uploaded, waiting, cleaned;
  SyncReport({
    this.downloaded = 0,
    this.uploaded = 0,
    this.waiting = 0,
    this.cleaned = 0,
  });
}

/// Durable receipts are published only after every decrypted chunk passes SHA-256.
/// Originals stay locally; the cloud is a relay, not the only copy or a backup.
class RelaySync {
  final ArchiveStore local;
  final RemoteStore remote;
  final String deviceName;
  final Serial _serial = Serial();
  RelaySync(this.local, this.remote, this.deviceName);

  Future<SyncReport> sync() => _serial.run(() async {
    final cipher = local.cipher, id = local.deviceId;
    for (final dir in ['', 'devices', 'events', 'blobs', 'acks', 'needs']) {
      await remote.ensure(dir);
    }
    await remote.put(
      'devices/$id.enc',
      await cipher.sealJson({'id': id, 'name': deviceName}, 'device:$id'),
      replace: true,
    );
    final devices = <String>{};
    for (final name in await remote.list('devices')) {
      if (!RegExp(r'^[a-f0-9]{32}\.enc$').hasMatch(name)) continue;
      final device = name.split('.').first,
          bytes = await remote.get('devices/$name');
      if (bytes == null) continue;
      final data = await cipher.openJson(bytes, 'device:$device');
      if (data['id'] != device) {
        throw const FormatException('Повреждён список устройств.');
      }
      devices.add(device);
    }
    final remoteEvents = (await remote.list('events'))
        .where((x) => RegExp(r'^[a-f0-9]{32}\.enc$').hasMatch(x))
        .map((x) => x.split('.').first)
        .toSet();
    final localIds = local.revisions.map((r) => r.id).toSet();
    // Merge batches before transferring originals: interrupted runs are resumable.
    final batch = <String, List<int>>{};
    for (final event in remoteEvents.difference(localIds)) {
      final bytes = await remote.get('events/$event.enc');
      if (bytes != null) batch[event] = bytes;
      if (batch.length >= 50) {
        await local.merge(batch);
        batch.clear();
      }
    }
    if (batch.isNotEmpty) await local.merge(batch);
    for (final event in local.revisions.where(
      (r) => !remoteEvents.contains(r.id),
    )) {
      await remote.put(
        'events/${event.id}.enc',
        await local.eventBytes(event.id),
      );
    }

    final blobs = <String, Map<String, dynamic>>{};
    for (final record in local.records) {
      // All concurrent branches stay recoverable, including trash until purged.
      for (final head in record.heads) {
        final b = head.data['blob'];
        if (b is Map) blobs[b['hash'] as String] = Map<String, dynamic>.from(b);
      }
    }
    var downloaded = 0, uploaded = 0, waiting = 0, cleaned = 0;
    for (final blob in blobs.values) {
      final hash = blob['hash'] as String;
      await remote.ensure('acks/$hash');
      await remote.ensure('needs/$hash');
      var have = await local.hasBlob(blob, verify: true);
      if (!have) {
        await remote.put(
          'needs/$hash/$id.enc',
          await cipher.sealJson({'device': id, 'hash': hash}, 'need:$hash:$id'),
          replace: true,
        );
        final chunks = await remote.list('blobs/$hash');
        final complete = List.generate(
          blob['chunks'] as int,
          (i) => '$i.enc',
        ).every(chunks.contains);
        if (complete) {
          await local.acceptBlob(blob, (i) async {
            final bytes = await remote.get('blobs/$hash/$i.enc');
            if (bytes == null) {
              throw RemoteFailure(
                'Файл ещё передаётся. Повторим синхронизацию.',
              );
            }
            return bytes;
          });
          have = true;
          downloaded++;
        }
      }
      if (!have) {
        waiting++;
        continue;
      }
      await remote.put(
        'acks/$hash/$id.enc',
        await cipher.sealJson({
          'device': id,
          'hash': hash,
          'size': blob['size'],
          'verified': true,
        }, 'ack:$hash:$id'),
      );
      await remote.delete('needs/$hash/$id.enc');
      final acked = <String>{};
      for (final name in await remote.list('acks/$hash')) {
        if (!RegExp(r'^[a-f0-9]{32}\.enc$').hasMatch(name)) continue;
        final device = name.split('.').first,
            bytes = await remote.get('acks/$hash/$name');
        if (bytes == null) continue;
        final ack = await cipher.openJson(bytes, 'ack:$hash:$device');
        if (ack['device'] == device &&
            ack['hash'] == hash &&
            ack['size'] == blob['size'] &&
            ack['verified'] == true) {
          acked.add(device);
        }
      }
      final needs = (await remote.list('needs/$hash'))
          .where((x) => RegExp(r'^[a-f0-9]{32}\.enc$').hasMatch(x))
          .toList();
      final delivered =
          devices.length >= 2 && devices.every(acked.contains) && needs.isEmpty;
      if (delivered) {
        // Only the remote relay folder is removed. Local encrypted chunks remain.
        if ((await remote.list('blobs')).contains(hash)) {
          await remote.delete('blobs/$hash');
          cleaned++;
        }
      } else {
        await remote.ensure('blobs/$hash');
        final chunks = (await remote.list('blobs/$hash')).toSet();
        var sent = false;
        for (var i = 0; i < (blob['chunks'] as int); i++) {
          if (chunks.contains('$i.enc')) continue;
          await remote.put(
            'blobs/$hash/$i.enc',
            await local.encryptedChunk(hash, i),
          );
          sent = true;
        }
        if (sent) uploaded++;
        waiting++;
      }
    }
    return SyncReport(
      downloaded: downloaded,
      uploaded: uploaded,
      waiting: waiting,
      cleaned: cleaned,
    );
  });
}
