import 'dart:convert';
import 'dart:io';

import 'package:izbrainoe/core/archive_store.dart';
import 'package:izbrainoe/core/backup.dart';
import 'package:izbrainoe/core/crypto_box.dart';
import 'package:izbrainoe/core/setup_transfer.dart';

Future<void> main(List<String> args) async {
  final setup = Map<String, dynamic>.from(
    jsonDecode(await File(args.single).readAsString()) as Map,
  );
  final raw =
      'izbrainoe-setup:${base64UrlEncode(utf8.encode(jsonEncode(setup))).replaceAll('=', '')}';
  final parsed = parseSetupCode(raw);
  final temp = await Directory.systemTemp.createTemp(
    'izbrainoe-transfer-check-',
  );
  try {
    final store = ArchiveStore(
      Directory('${temp.path}/archive'),
      await ArchiveCipher.fromCode(parsed['archive_code'] as String),
      newId(),
    );
    await store.load();
    final bundle = await downloadSetupArchive(parsed, temp);
    if (bundle == null) throw StateError('No transfer');
    await ArchiveBackup.import(bundle, store);
    var originals = 0;
    for (final r in store.records) {
      if (r.blob != null) {
        if (!await store.hasBlob(r.blob!, verify: true)) {
          throw StateError('Integrity');
        }
        originals++;
      }
    }
    stdout.writeln(
      jsonEncode({
        'records': store.records.length,
        'verified_originals': originals,
        'vault_encrypted': store.records.any((r) => r.kind == 'vault'),
      }),
    );
  } finally {
    await temp.delete(recursive: true);
  }
}
