import 'dart:convert';
import 'dart:io';

import 'package:image/image.dart' as img;
import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/crypto_box.dart';
import 'package:izbrainoe/core/intelligence.dart';

void main() {
  test('live synthetic vision through configured Provod', () async {
    const path = String.fromEnvironment('LIVE_SETUP_PATH');
    if (path.isEmpty) return;
    final setup = Map<String, dynamic>.from(
      jsonDecode(await File(path).readAsString()) as Map,
    );
    final temp = await Directory.systemTemp.createTemp('izbrainoe-live-');
    try {
      final ledger = UsageLedger(
        File('${File(path).parent.path}/live-check-usage.enc'),
        await ArchiveCipher.fromCode(setup['archive_code'] as String),
      );
      await ledger.load();
      final client = GeminiClient(
        ledger,
        apiKey: setup['provod_key'] as String,
        monthlyLimit: 10,
      );
      final picture = img.Image(width: 900, height: 380);
      img.fill(picture, color: img.ColorRgb8(255, 255, 255));
      img.drawString(
        picture,
        'Linear algebra',
        font: img.arial48,
        x: 30,
        y: 25,
        color: img.ColorRgb8(0, 0, 0),
      );
      img.drawString(
        picture,
        'det(A) = a*d - b*c',
        font: img.arial48,
        x: 30,
        y: 120,
        color: img.ColorRgb8(0, 0, 0),
      );
      img.drawString(
        picture,
        'Integral x^2 dx = x^3/3 + C',
        font: img.arial24,
        x: 30,
        y: 245,
        color: img.ColorRgb8(0, 0, 0),
      );
      final response = await client.analyze(
        {'filename': 'scan.jpg', 'kind': 'photo', 'body': '', 'context': ''},
        PreparedFile(images: [img.encodeJpg(picture, quality: 80)]),
        ['Учёба', 'Высшая математика', 'IT'],
        [],
      );
      stdout.writeln(
        jsonEncode({
          'folder': response['folder'],
          'confidence': response['confidence'],
          'recognized_text_present':
              (response['recognized_text'] as String).isNotEmpty,
          'estimated_rub': ledger.total,
        }),
      );
      client.client.close();
    } finally {
      await temp.delete(recursive: true);
    }
  }, timeout: const Timeout(Duration(seconds: 60)));
}
