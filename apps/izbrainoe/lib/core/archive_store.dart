import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as hashes;
import 'package:path/path.dart' as p;

import 'crypto_box.dart';

class Serial {
  Future<void> _tail = Future.value();
  Future<T> run<T>(Future<T> Function() action) {
    final result = _tail.then((_) => action());
    _tail = result.then<void>((_) {}, onError: (Object _, StackTrace _) {});
    return result;
  }
}

Future<void> atomicWrite(File file, List<int> bytes) async {
  await file.parent.create(recursive: true);
  final temp = File('${file.path}.${newId()}.part');
  try {
    await temp.writeAsBytes(bytes, flush: true);
    await temp.rename(file.path);
  } finally {
    if (await temp.exists()) await temp.delete();
  }
}

String canonicalFolder(String value) {
  var s = value.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (s.isEmpty) return 'Неразобранное';
  final lower = s.toLowerCase().replaceAll('ё', 'е');
  if (RegExp(
    r'вышмат|матан|высшая математика|линейная алгебра|математический анализ|определители матриц',
  ).hasMatch(lower)) {
    return 'Высшая математика';
  }
  if (RegExp(
    r'^(it|ит|программирование|разработка|информационные технологии)(\s*/|$)',
  ).hasMatch(lower)) {
    return 'IT';
  }
  if (['вуз', 'учеба', 'университет'].contains(lower)) return 'Учёба';
  if (lower.startsWith('архивы') || lower == 'zip') return 'ZIP';
  final parts = s
      .split('/')
      .map((x) => x.trim())
      .where((x) => x.isNotEmpty)
      .toList();
  if (parts.length > 1) {
    final known = parts.where(
      (x) => RegExp(
        r'математ|физик|хими|русский язык|английский язык',
        caseSensitive: false,
      ).hasMatch(x),
    );
    s = known.isNotEmpty ? known.first : parts.first;
  }
  return s.substring(0, min(s.length, 50));
}

class Revision {
  final String id, record, device;
  final int clock, created;
  final List<String> parents;
  final Map<String, dynamic> data;
  Revision({
    required this.id,
    required this.record,
    required this.device,
    required this.clock,
    required this.created,
    required this.parents,
    required this.data,
  });
  factory Revision.fromJson(Map<String, dynamic> j) {
    if (j['v'] != 1 ||
        !validId(j['id'] as String? ?? '') ||
        !validId(j['record'] as String? ?? '') ||
        !validId(j['device'] as String? ?? '') ||
        j['clock'] is! int ||
        (j['clock'] as int) < 1 ||
        j['created'] is! int ||
        j['parents'] is! List ||
        (j['parents'] as List).length > 100 ||
        j['data'] is! Map) {
      throw const FormatException('Повреждена запись архива.');
    }
    final parents = List<String>.from(j['parents'] as List);
    if (parents.any((x) => !validId(x) || x == j['id'])) {
      throw const FormatException('Неверная история записи.');
    }
    final data = Map<String, dynamic>.from(j['data'] as Map);
    final blob = data['blob'];
    if (blob != null &&
        (blob is! Map ||
            !validDigest(blob['hash'] as String? ?? '') ||
            blob['size'] is! int ||
            (blob['size'] as int) < 0 ||
            (blob['size'] as int) > 2 * 1024 * 1024 * 1024 ||
            blob['chunks'] is! int ||
            (blob['chunks'] as int) !=
                ((blob['size'] as int) / ArchiveStore.chunkSize).ceil())) {
      throw const FormatException('Неверные сведения о файле.');
    }
    if (data['folder'] is String) {
      data['folder'] = canonicalFolder(data['folder'] as String);
    }
    return Revision(
      id: j['id'] as String,
      record: j['record'] as String,
      device: j['device'] as String,
      clock: j['clock'] as int,
      created: j['created'] as int,
      parents: parents,
      data: data,
    );
  }
  Map<String, dynamic> toJson() => {
    'v': 1,
    'id': id,
    'record': record,
    'device': device,
    'clock': clock,
    'created': created,
    'parents': parents,
    'data': data,
  };
}

class ArchiveRecord {
  final String id;
  final List<Revision> heads;
  ArchiveRecord(this.id, this.heads);
  Revision get current => heads.last;
  Map<String, dynamic> get data => current.data;
  bool get conflict => heads.length > 1;
  bool get deleted => heads.every((x) => x.data['deleted'] == true);
  bool get credential => data['kind'] == 'credential';
  String get title => data['title'] as String? ?? 'Без названия';
  String get folder => data['folder'] as String? ?? 'Неразобранное';
  String get kind => data['kind'] as String? ?? 'note';
  Map<String, dynamic>? get blob => data['blob'] == null
      ? null
      : Map<String, dynamic>.from(data['blob'] as Map);
}

class ArchiveStore {
  static const chunkSize = 1024 * 1024;
  final Directory directory;
  final ArchiveCipher cipher;
  final String deviceId;
  final Serial _serial = Serial();
  final Map<String, Revision> _revisions = {};
  int _clock = 0;
  ArchiveStore(this.directory, this.cipher, this.deviceId);
  Directory get eventDirectory => Directory(p.join(directory.path, 'events'));
  Directory blobDirectory(String hash) {
    if (!validDigest(hash)) {
      throw const FormatException('Неверный идентификатор файла.');
    }
    return Directory(p.join(directory.path, 'blobs', hash));
  }

  Future<void> load() => _serial.run(() async {
    await eventDirectory.create(recursive: true);
    await Directory(p.join(directory.path, 'blobs')).create(recursive: true);
    await for (final f in eventDirectory.list()) {
      if (f is! File ||
          !RegExp(r'^[a-f0-9]{32}\.enc$').hasMatch(p.basename(f.path))) {
        continue;
      }
      final id = p.basenameWithoutExtension(f.path);
      final r = Revision.fromJson(
        await cipher.openJson(await f.readAsBytes(), 'event:$id'),
      );
      if (r.id != id) throw const FormatException('Подмена записи.');
      _revisions[id] = r;
      _clock = max(_clock, r.clock);
    }
    _validateGraph(_revisions.values);
  });
  static void _validateGraph(Iterable<Revision> revisions) {
    final index = {for (final r in revisions) r.id: r};
    for (final r in index.values) {
      for (final parent in r.parents) {
        final before = index[parent];
        if (before != null &&
            (before.record != r.record || before.clock >= r.clock)) {
          throw const FormatException('Неверная последовательность изменений.');
        }
      }
    }
  }

  List<Revision> get revisions => List.unmodifiable(_revisions.values);
  List<ArchiveRecord> get records {
    final byRecord = <String, List<Revision>>{}, superseded = <String>{};
    for (final r in _revisions.values) {
      byRecord.putIfAbsent(r.record, () => []).add(r);
      superseded.addAll(r.parents);
    }
    final result =
        byRecord.entries
            .map((e) {
              final heads =
                  e.value.where((r) => !superseded.contains(r.id)).toList()
                    ..sort(
                      (a, b) => a.clock != b.clock
                          ? a.clock.compareTo(b.clock)
                          : a.id.compareTo(b.id),
                    );
              return ArchiveRecord(e.key, heads);
            })
            .where((x) => x.heads.isNotEmpty)
            .toList()
          ..sort(
            (a, b) => (b.data['created'] as int? ?? b.current.created)
                .compareTo(a.data['created'] as int? ?? a.current.created),
          );
    return result;
  }

  ArchiveRecord? get(String id) {
    for (final r in records) {
      if (r.id == id) return r;
    }
    return null;
  }

  Future<ArchiveRecord> save(
    Map<String, dynamic> data, {
    String? recordId,
    List<String>? expectedHeads,
  }) => _serial.run(() async {
    final id = recordId ?? newId(),
        existing = recordId == null ? null : get(recordId);
    final parents = existing?.heads.map((x) => x.id).toList() ?? [];
    if (expectedHeads != null &&
        (parents.length != expectedHeads.length ||
            !parents.toSet().containsAll(expectedHeads))) {
      throw const FormatException(
        'Запись изменилась. Открой её заново перед сохранением.',
      );
    }
    final now = DateTime.now().millisecondsSinceEpoch,
        clean = Map<String, dynamic>.from(data);
    if (clean['kind'] != 'credential') {
      clean['folder'] = canonicalFolder(clean['folder'] as String? ?? '');
    }
    clean['created'] ??= existing?.data['created'] ?? now;
    final revision = Revision.fromJson({
      'v': 1,
      'id': newId(),
      'record': id,
      'device': deviceId,
      'clock': ++_clock,
      'created': now,
      'parents': parents,
      'data': clean,
    });
    await atomicWrite(
      File(p.join(eventDirectory.path, '${revision.id}.enc')),
      await cipher.sealJson(revision.toJson(), 'event:${revision.id}'),
    );
    _revisions[revision.id] = revision;
    return get(id)!;
  });

  /// Persist immutable events before exposing them. Independent edits remain heads.
  Future<void> merge(Map<String, List<int>> incoming) => _serial.run(() async {
    final additions = <String, Revision>{};
    for (final e in incoming.entries) {
      if (!validId(e.key)) throw const FormatException('Неверное имя записи.');
      if (_revisions.containsKey(e.key)) continue;
      final revision = Revision.fromJson(
        await cipher.openJson(e.value, 'event:${e.key}'),
      );
      if (revision.id != e.key) throw const FormatException('Подмена записи.');
      additions[e.key] = revision;
    }
    _validateGraph([..._revisions.values, ...additions.values]);
    for (final r in additions.values) {
      await atomicWrite(
        File(p.join(eventDirectory.path, '${r.id}.enc')),
        incoming[r.id]!,
      );
      _revisions[r.id] = r;
      _clock = max(_clock, r.clock);
    }
  });
  Future<Uint8List> eventBytes(String id) =>
      File(p.join(eventDirectory.path, '$id.enc')).readAsBytes();
  Future<Map<String, dynamic>> importFile(File source) async {
    final size = await source.length();
    if (size > 2 * 1024 * 1024 * 1024) {
      throw const FormatException(
        'Файл больше 2 ГБ. Раздели его перед сохранением.',
      );
    }
    final hash = (await hashes.sha256.bind(source.openRead()).first).toString();
    final manifest = <String, dynamic>{
      'hash': hash,
      'size': size,
      'chunks': (size / chunkSize).ceil(),
    };
    if (await hasBlob(manifest)) return manifest;
    final staging = Directory(
      p.join(directory.path, 'blobs', '.${newId()}.part'),
    );
    await staging.create(recursive: true);
    final input = await source.open();
    try {
      var number = 0;
      while (true) {
        final block = await input.read(chunkSize);
        if (block.isEmpty) break;
        await atomicWrite(
          File(p.join(staging.path, '$number.enc')),
          await cipher.seal(block, 'blob:$hash:$number'),
        );
        number++;
      }
      if (number != manifest['chunks'] || await source.length() != size) {
        throw const FormatException('Файл изменился во время сохранения.');
      }
      await _verifyDirectory(staging, manifest);
      await _serial.run(() async {
        final target = blobDirectory(hash);
        if (await target.exists()) await target.delete(recursive: true);
        await staging.rename(target.path);
      });
      return manifest;
    } finally {
      await input.close();
      if (await staging.exists()) await staging.delete(recursive: true);
    }
  }

  Future<void> _verifyDirectory(
    Directory folder,
    Map<String, dynamic> blob,
  ) async {
    final sink = _DigestSink();
    // Keep one incremental digest sink: no whole-file plaintext buffer.
    final digestInput = hashes.sha256.startChunkedConversion(sink);
    var length = 0;
    for (var i = 0; i < (blob['chunks'] as int); i++) {
      final bytes = await cipher.open(
        await File(p.join(folder.path, '$i.enc')).readAsBytes(),
        'blob:${blob['hash']}:$i',
      );
      if (bytes.isEmpty ||
          bytes.length > chunkSize ||
          (i < (blob['chunks'] as int) - 1 && bytes.length != chunkSize)) {
        throw const FormatException('Повреждён фрагмент файла.');
      }
      length += bytes.length;
      digestInput.add(bytes);
    }
    digestInput.close();
    if (length != blob['size'] || sink.value.toString() != blob['hash']) {
      throw const FormatException('Проверка целостности файла не пройдена.');
    }
  }

  Future<bool> hasBlob(Map<String, dynamic> blob, {bool verify = false}) async {
    final folder = blobDirectory(blob['hash'] as String);
    if (!await folder.exists()) return false;
    for (var i = 0; i < (blob['chunks'] as int); i++) {
      if (!await File(p.join(folder.path, '$i.enc')).exists()) return false;
    }
    if (verify) {
      try {
        await _verifyDirectory(folder, blob);
      } catch (_) {
        return false;
      }
    }
    return true;
  }

  Future<Uint8List> encryptedChunk(String hash, int i) =>
      File(p.join(blobDirectory(hash).path, '$i.enc')).readAsBytes();
  Future<void> acceptBlob(
    Map<String, dynamic> blob,
    Future<List<int>> Function(int) getChunk,
  ) async {
    final staging = Directory(
      p.join(directory.path, 'blobs', '.${newId()}.part'),
    );
    await staging.create(recursive: true);
    try {
      for (var i = 0; i < (blob['chunks'] as int); i++) {
        final bytes = await getChunk(i);
        if (bytes.length > chunkSize + 29) {
          throw const FormatException('Слишком большой фрагмент.');
        }
        await atomicWrite(File(p.join(staging.path, '$i.enc')), bytes);
      }
      await _verifyDirectory(staging, blob);
      await _serial.run(() async {
        final target = blobDirectory(blob['hash'] as String);
        if (await target.exists()) await target.delete(recursive: true);
        await staging.rename(target.path);
      });
    } finally {
      if (await staging.exists()) await staging.delete(recursive: true);
    }
  }

  Future<File> materialize(
    Map<String, dynamic> blob,
    Directory temporary,
    String filename,
  ) async {
    await temporary.create(recursive: true);
    final safe = p
        .basename(filename)
        .replaceAll(RegExp(r'[^\wа-яА-ЯёЁ .()\-]'), '_');
    final output = File(
      p.join(temporary.path, '${newId()}-${safe.isEmpty ? 'file' : safe}'),
    );
    final writer = await output.open(mode: FileMode.write);
    try {
      for (var i = 0; i < (blob['chunks'] as int); i++) {
        await writer.writeFrom(
          await cipher.open(
            await encryptedChunk(blob['hash'] as String, i),
            'blob:${blob['hash']}:$i',
          ),
        );
      }
      await writer.flush();
      await writer.close();
      if (await output.length() != blob['size'] ||
          (await hashes.sha256.bind(output.openRead()).first).toString() !=
              blob['hash']) {
        throw const FormatException('Повреждён оригинал файла.');
      }
      return output;
    } catch (_) {
      try {
        await writer.close();
      } catch (_) {}
      if (await output.exists()) await output.delete();
      rethrow;
    }
  }
}

class _DigestSink implements Sink<hashes.Digest> {
  hashes.Digest? value;
  @override
  void add(hashes.Digest data) {
    value = data;
  }

  @override
  void close() {}
}
