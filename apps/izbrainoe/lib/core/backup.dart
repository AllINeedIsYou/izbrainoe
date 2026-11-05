import 'dart:convert';
import 'dart:io';

import 'package:archive/archive_io.dart';
import 'package:path/path.dart' as p;

import 'archive_store.dart';
import 'crypto_box.dart';

class ArchiveBackup {
  static Future<File> export(ArchiveStore store, Directory temp) async {
    await temp.create(recursive: true);
    final out = File(
      p.join(
        temp.path,
        'Изbrainое-${DateTime.now().toIso8601String().substring(0, 10)}-${newId().substring(0, 6)}.izbrainoe',
      ),
    );
    final encoder = ZipFileEncoder();
    encoder.create(out.path);
    try {
      encoder.addArchiveFile(
        ArchiveFile.string(
          'manifest.json',
          jsonEncode({
            'app': 'izbrainoe',
            'v': 1,
            'archive': store.cipher.archiveId,
          }),
        ),
      );
      for (final r in store.revisions) {
        await encoder.addFile(
          File(p.join(store.eventDirectory.path, '${r.id}.enc')),
          'events/${r.id}.enc',
        );
      }
      final seen = <String>{};
      for (final r in store.records) {
        for (final head in r.heads) {
          final blob = head.data['blob'];
          if (blob is! Map || !seen.add(blob['hash'] as String)) continue;
          final manifest = Map<String, dynamic>.from(blob);
          if (!await store.hasBlob(manifest, verify: true)) {
            throw const FormatException(
              'Не все оригиналы доступны. Дождись синхронизации перед резервным копированием.',
            );
          }
          for (var i = 0; i < (blob['chunks'] as int); i++) {
            await encoder.addFile(
              File(
                p.join(
                  store.blobDirectory(blob['hash'] as String).path,
                  '$i.enc',
                ),
              ),
              'blobs/${blob['hash']}/$i.enc',
            );
          }
        }
      }
    } finally {
      encoder.close();
    }
    return out;
  }

  static Future<void> import(
    File file,
    ArchiveStore store, {
    void Function(int completed, int total)? onProgress,
  }) async {
    if (await file.length() > 4 * 1024 * 1024 * 1024) {
      throw const FormatException('Слишком большая резервная копия.');
    }
    final input = InputFileStream(file.path);
    final staging = Directory(
      p.join(store.directory.path, '.restore-${newId()}'),
    );
    try {
      final zip = ZipDecoder().decodeStream(input, verify: true);
      if (zip.files.length > 50000 ||
          zip.files.fold<int>(0, (s, f) => s + f.size) >
              4 * 1024 * 1024 * 1024) {
        throw const FormatException('Превышен объём резервной копии.');
      }
      final header = zip.findFile('manifest.json');
      if (header == null || header.size > 4096) {
        throw const FormatException('Это не резервная копия Изbrainого.');
      }
      final manifest = jsonDecode(utf8.decode(header.readBytes()!)) as Map;
      if (manifest['v'] != 1 || manifest['archive'] != store.cipher.archiveId) {
        throw const FormatException(
          'Копия относится к другому архиву. Подключи его код на пустом устройстве.',
        );
      }
      await staging.create(recursive: true);
      for (final f in zip.files) {
        if (!f.isFile || f.name == 'manifest.json') continue;
        if (!RegExp(
              r'^(events/[a-f0-9]{32}\.enc|blobs/[a-f0-9]{64}/[0-9]{1,5}\.enc)$',
            ).hasMatch(f.name) ||
            f.size > 8 * 1024 * 1024) {
          throw const FormatException('Неверная структура резервной копии.');
        }
        final target = File(p.join(staging.path, f.name));
        await target.parent.create(recursive: true);
        final output = OutputFileStream(target.path);
        try {
          f.writeContent(output);
        } finally {
          output.close();
        }
      }
      final restored = ArchiveStore(staging, store.cipher, newId());
      await restored.load();
      final blobs = <String, Map<String, dynamic>>{};
      for (final r in restored.records) {
        for (final head in r.heads) {
          final blob = head.data['blob'];
          if (blob is Map) {
            blobs[blob['hash'] as String] = Map<String, dynamic>.from(blob);
          }
        }
      }
      // Authenticate everything before touching the destination archive.
      var verified = 0;
      onProgress?.call(0, blobs.length * 2 + 1);
      for (final blob in blobs.values) {
        if (!await restored.hasBlob(blob, verify: true)) {
          throw const FormatException(
            'В резервной копии отсутствует или повреждён оригинал.',
          );
        }
        onProgress?.call(++verified, blobs.length * 2 + 1);
      }
      for (final blob in blobs.values) {
        if (!await store.hasBlob(blob, verify: true)) {
          await store.acceptBlob(
            blob,
            (i) => restored.encryptedChunk(blob['hash'] as String, i),
          );
        }
        onProgress?.call(++verified, blobs.length * 2 + 1);
      }
      final events = <String, List<int>>{};
      for (final r in restored.revisions) {
        events[r.id] = await restored.eventBytes(r.id);
      }
      await store.merge(events);
      onProgress?.call(blobs.length * 2 + 1, blobs.length * 2 + 1);
    } finally {
      input.close();
      if (await staging.exists()) await staging.delete(recursive: true);
    }
  }
}
