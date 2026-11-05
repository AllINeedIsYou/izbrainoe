import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/archive_store.dart';
import 'package:izbrainoe/core/backup.dart';
import 'package:izbrainoe/core/crypto_box.dart';
import 'package:izbrainoe/core/relay.dart';

class MemoryRemote extends RemoteStore {
  final Map<String, Uint8List> objects = {};
  final Set<String> directories = {};
  int? interruptAfter;
  int writes = 0;
  @override
  Future<void> ensure(String dir) async {
    directories.add(dir);
  }

  @override
  Future<List<String>> list(String dir) async {
    final prefix = dir.isEmpty ? '' : '$dir/';
    return {...objects.keys, ...directories}
        .where((key) => key.startsWith(prefix) && key != dir)
        .map((key) => key.substring(prefix.length).split('/').first)
        .where((s) => s.isNotEmpty)
        .toSet()
        .toList();
  }

  @override
  Future<Uint8List?> get(String path) async =>
      objects[path] == null ? null : Uint8List.fromList(objects[path]!);
  @override
  Future<void> put(String path, List<int> bytes, {bool replace = false}) async {
    if (path.startsWith('blobs/') &&
        interruptAfter != null &&
        ++writes >= interruptAfter!) {
      throw RemoteFailure('offline');
    }
    if (!objects.containsKey(path) || replace) {
      objects[path] = Uint8List.fromList(bytes);
    }
  }

  @override
  Future<void> delete(String path) async {
    objects.removeWhere((k, v) => k == path || k.startsWith('$path/'));
    directories.removeWhere((k) => k == path || k.startsWith('$path/'));
  }
}

void main() {
  late Directory temp;
  late ArchiveCipher cipher;
  Future<ArchiveStore> device(String name) async {
    final s = ArchiveStore(Directory('${temp.path}/$name'), cipher, newId());
    await s.load();
    return s;
  }

  Future<ArchiveRecord> add(ArchiveStore s, {int length = 3000}) async {
    final f = File('${temp.path}/original.bin');
    await f.writeAsBytes(List.generate(length, (i) => i % 251));
    return s.save({
      'kind': 'document',
      'title': 'Секретный документ',
      'folder': 'Высшая математика / Материалы',
      'blob': await s.importFile(f),
    });
  }

  setUp(() async {
    temp = await Directory.systemTemp.createTemp('izbrainoe-test-');
    cipher = await ArchiveCipher.fromCode(ArchiveCipher.createCode());
  });
  tearDown(() async {
    await temp.delete(recursive: true);
  });
  test(
    'encryption authenticates purpose, wrong keys and damaged bytes',
    () async {
      final sealed = await cipher.seal(utf8.encode('private original'), 'test');
      expect(
        utf8.decode(await cipher.open(sealed, 'test')),
        'private original',
      );
      expect(() => cipher.open(sealed, 'other'), throwsA(anything));
      final other = await ArchiveCipher.fromCode(ArchiveCipher.createCode());
      expect(() => other.open(sealed, 'test'), throwsA(anything));
      sealed.last ^= 1;
      expect(() => cipher.open(sealed, 'test'), throwsA(anything));
    },
  );
  test('encrypted persistence, file integrity and restart', () async {
    final a = await device('a'), r = await add(await device('b'));
    final f = File('${temp.path}/small');
    await f.writeAsString('confidential original');
    final saved = await a.save({
      'kind': 'document',
      'title': 'Личный файл',
      'folder': 'Вышмат / Учебники',
      'blob': await a.importFile(f),
    });
    final reopened = ArchiveStore(a.directory, cipher, a.deviceId);
    await reopened.load();
    expect(reopened.records.single.folder, 'Высшая математика');
    final output = await reopened.materialize(
      saved.blob!,
      Directory('${temp.path}/clear'),
      '../../original.txt',
    );
    expect(await output.readAsString(), 'confidential original');
    expect(output.parent.path, '${temp.path}/clear');
    expect(
      utf8.decode(await a.eventBytes(saved.current.id), allowMalformed: true),
      isNot(contains('Личный файл')),
    );
    expect(r.folder, 'Высшая математика');
    final chunk = File(
      '${a.blobDirectory(saved.blob!['hash'] as String).path}/0.enc',
    );
    final corrupted = await chunk.readAsBytes();
    corrupted.last ^= 1;
    await chunk.writeAsBytes(corrupted);
    expect(await a.hasBlob(saved.blob!, verify: true), false);
    expect(
      () => a.materialize(saved.blob!, Directory('${temp.path}/clear'), 'file'),
      throwsA(anything),
    );
  });
  test(
    'cloud keeps original until a second complete verified device exists',
    () async {
      final a = await device('a'),
          b = await device('b'),
          remote = MemoryRemote();
      final r = await add(a);
      final hash = r.blob!['hash'];
      await RelaySync(a, remote, 'Android').sync();
      expect(await remote.list('blobs/$hash'), ['0.enc']);
      await RelaySync(a, remote, 'Android').sync();
      expect(await remote.list('blobs/$hash'), ['0.enc']);
      await RelaySync(b, remote, 'Mac').sync();
      expect(await b.hasBlob(r.blob!, verify: true), true);
      expect(await remote.list('blobs/$hash'), isEmpty);
      expect(await a.hasBlob(r.blob!, verify: true), true);
      await RelaySync(a, remote, 'Android').sync();
      expect(await remote.list('blobs/$hash'), isEmpty);
    },
  );
  test(
    'corrupt download never acknowledges or removes last relay copy',
    () async {
      final a = await device('a'),
          b = await device('b'),
          remote = MemoryRemote();
      final r = await add(a);
      final hash = r.blob!['hash'];
      await RelaySync(a, remote, 'Android').sync();
      remote.objects['blobs/$hash/0.enc']!.last ^= 1;
      expect(() => RelaySync(b, remote, 'Mac').sync(), throwsA(anything));
      expect(remote.objects.containsKey('acks/$hash/${b.deviceId}.enc'), false);
      expect(remote.objects.containsKey('blobs/$hash/0.enc'), true);
      expect(await b.hasBlob(r.blob!, verify: true), false);
    },
  );
  test('interrupted multi-chunk upload resumes without data loss', () async {
    final a = await device('a'), b = await device('b'), remote = MemoryRemote();
    final r = await add(a, length: ArchiveStore.chunkSize * 2 + 17);
    remote.interruptAfter = 2;
    await expectLater(
      () => RelaySync(a, remote, 'Android').sync(),
      throwsA(isA<RemoteFailure>()),
    );
    remote.interruptAfter = null;
    await RelaySync(a, remote, 'Android').sync();
    await RelaySync(b, remote, 'Mac').sync();
    expect(await b.hasBlob(r.blob!, verify: true), true);
  });
  test(
    'new device can request originals after cloud relay was cleaned',
    () async {
      final a = await device('a'),
          b = await device('b'),
          c = await device('c'),
          remote = MemoryRemote();
      final r = await add(a);
      await RelaySync(a, remote, 'A').sync();
      await RelaySync(b, remote, 'B').sync();
      final pending = await RelaySync(c, remote, 'C').sync();
      expect(pending.waiting, 1);
      expect(await c.hasBlob(r.blob!), false);
      await RelaySync(a, remote, 'A').sync();
      await RelaySync(c, remote, 'C').sync();
      expect(await c.hasBlob(r.blob!, verify: true), true);
      expect(await remote.list('blobs'), isEmpty);
    },
  );
  test(
    'offline edits remain two heads; stale saves cannot overwrite them',
    () async {
      final a = await device('a'),
          b = await device('b'),
          remote = MemoryRemote();
      final r = await add(a);
      await RelaySync(a, remote, 'A').sync();
      await RelaySync(b, remote, 'B').sync();
      await a.save({
        ...r.data,
        'context': 'Исправление с телефона',
      }, recordId: r.id);
      await b.save({...r.data, 'context': 'Исправление с Mac'}, recordId: r.id);
      await RelaySync(a, remote, 'A').sync();
      await RelaySync(b, remote, 'B').sync();
      await RelaySync(a, remote, 'A').sync();
      final conflict = a.get(r.id)!;
      expect(conflict.heads.length, 2);
      expect(conflict.heads.map((h) => h.data['context']).toSet(), {
        'Исправление с телефона',
        'Исправление с Mac',
      });
      expect(
        () => a.save(r.data, recordId: r.id, expectedHeads: [r.current.id]),
        throwsA(isA<FormatException>()),
      );
      await a.save(
        conflict.heads.first.data,
        recordId: r.id,
        expectedHeads: conflict.heads.map((h) => h.id).toList(),
      );
      expect(a.get(r.id)!.conflict, false);
    },
  );
  test('concurrent delete and edit are visible as conflict', () async {
    final a = await device('a'), b = await device('b'), remote = MemoryRemote();
    final r = await add(a);
    await RelaySync(a, remote, 'A').sync();
    await RelaySync(b, remote, 'B').sync();
    await a.save({...r.data, 'deleted': true}, recordId: r.id);
    await b.save({...r.data, 'context': 'Сохранить'}, recordId: r.id);
    await RelaySync(a, remote, 'A').sync();
    await RelaySync(b, remote, 'B').sync();
    expect(b.get(r.id)!.deleted, false);
    expect(b.get(r.id)!.conflict, true);
  });
  test('encrypted full backup restores originals and metadata', () async {
    final a = await device('a'), b = await device('b');
    final r = await add(a);
    final backup = await ArchiveBackup.export(
      a,
      Directory('${temp.path}/backups'),
    );
    await ArchiveBackup.import(backup, b);
    expect(b.get(r.id)!.title, r.title);
    expect(await b.hasBlob(r.blob!, verify: true), true);
    final wrong = ArchiveStore(
      Directory('${temp.path}/wrong'),
      await ArchiveCipher.fromCode(ArchiveCipher.createCode()),
      newId(),
    );
    await wrong.load();
    expect(
      () => ArchiveBackup.import(backup, wrong),
      throwsA(isA<FormatException>()),
    );
    expect(wrong.records, isEmpty);
  });
  test('all topic aliases produce one stable folder', () {
    for (final s in [
      'вышмат',
      'Матан',
      'Высшая математика / Учебники',
      'Высшая математика / Материалы',
      'Линейная алгебра',
    ]) {
      expect(canonicalFolder(s), 'Высшая математика');
    }
    for (final s in ['IT', 'Программирование / Проекты', 'Разработка']) {
      expect(canonicalFolder(s), 'IT');
    }
  });
}
