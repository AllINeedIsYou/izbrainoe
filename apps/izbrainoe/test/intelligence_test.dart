import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:image/image.dart' as img;
import 'package:izbrainoe/core/archive_store.dart';
import 'package:izbrainoe/core/crypto_box.dart';
import 'package:izbrainoe/core/intelligence.dart';

void main() {
  late Directory temp;
  late UsageLedger ledger;
  setUp(() async {
    temp = await Directory.systemTemp.createTemp('izb-ai-');
    ledger = UsageLedger(
      File('${temp.path}/usage.enc'),
      await ArchiveCipher.fromCode(ArchiveCipher.createCode()),
    );
    await ledger.load();
  });
  tearDown(() async {
    await temp.delete(recursive: true);
  });
  test('vision request includes picture, filename and context; text secrets redacted', () async {
    Map? request;
    final client = GeminiClient(
      ledger,
      apiKey: 'test-only',
      client: MockClient((r) async {
        request = jsonDecode(r.body) as Map;
        return http.Response(
          jsonEncode({
            'choices': [
              {
                'finish_reason': 'stop',
                'message': {
                  'content': jsonEncode({
                    'folder': 'Высшая математика / Учебники',
                    'summary': 'Страница с формулами',
                    'document_type': 'конспект',
                    'topics': ['матрицы'],
                    'recognized_text': 'Δ=ad-bc',
                    'visual_summary': 'Формулы и матрицы',
                    'confidence': .98,
                    'needs_context': false,
                    'research_topic': '',
                  }),
                },
              },
            ],
            'usage': {'prompt_tokens': 1200, 'completion_tokens': 180},
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    final answer = await client.analyze(
      {
        'filename': 'матрицы.jpg',
        'body': 'api_key=not-for-model',
        'kind': 'photo',
        'context': 'Для зачёта',
      },
      PreparedFile(
        images: [
          Uint8List.fromList([1, 2, 3]),
        ],
      ),
      ['Высшая математика'],
      [],
    );
    expect(jsonEncode(request), contains('data:image/jpeg;base64,'));
    expect(jsonEncode(request), contains('Для зачёта'));
    expect(jsonEncode(request), isNot(contains('not-for-model')));
    expect(answer['folder'], 'Высшая математика');
    expect(ledger.total, closeTo(.12774, .00001));
  });
  test(
    'budget reservation is durable and prevents concurrent overspend',
    () async {
      final attempts = await Future.wait(
        List.generate(4, (_) async {
          try {
            return await ledger.reserve(1000, 1000, .5);
          } catch (_) {
            return null;
          }
        }),
      );
      expect(attempts.whereType<String>().length, 1);
      final reloaded = UsageLedger(ledger.file, ledger.cipher);
      await reloaded.load();
      expect(reloaded.total, closeTo(.364, .000001));
    },
  );
  test(
    'rejected API call releases reservation; unknown failure keeps it',
    () async {
      final bad = GeminiClient(
        ledger,
        apiKey: 'test',
        client: MockClient((_) async => http.Response('{}', 401)),
      );
      await expectLater(
        () => bad.ask('test', {}, analysisSchema),
        throwsA(isA<AiFailure>()),
      );
      expect(ledger.total, 0);
      final timeout = GeminiClient(
        ledger,
        apiKey: 'test',
        client: MockClient((_) async => throw const SocketException('offline')),
      );
      await expectLater(
        () => timeout.ask('test', {}, analysisSchema),
        throwsA(isA<AiFailure>()),
      );
      expect(ledger.total, greaterThan(0));
    },
  );
  test('structured files are read, not classified only by extension', () async {
    final f = File('${temp.path}/config.json');
    await f.writeAsString(
      '{"server":"localhost","port":8080,"password":"secret-value"}',
    );
    final prepared = await prepareFile(f, 'config.json');
    expect(prepared.text, contains('8080'));
    expect(prepared.text, isNot(contains('secret-value')));
    expect(fallbackFolder('config.json'), 'IT');
    final office = Archive()
      ..addFile(
        ArchiveFile.string(
          'word/document.xml',
          '<w:document xmlns:w="urn:w"><w:p><w:t>СОГЛАСИЕ на водительский экзамен</w:t></w:p></w:document>',
        ),
      );
    final text = readArchive({
      'bytes': Uint8List.fromList(ZipEncoder().encode(office)),
      'ext': '.docx',
    });
    expect(text.text, contains('водительский экзамен'));
    expect(fallbackFolder('agreement.docx', body: text.text), 'Авто');
  });
  test('photo downscale preserves orientation and bounds', () {
    final source = img.Image(width: 2200, height: 1000);
    final bytes = resizePhoto(img.encodeJpg(source));
    final preview = img.decodeJpg(bytes)!;
    expect(preview.width, 1536);
    expect(preview.height, 698);
    expect(bytes.length, lessThan(500000));
  });
  test('search sends no vault records and accepts only existing ids', () async {
    final archive = ArchiveStore(
      Directory('${temp.path}/a'),
      ledger.cipher,
      newId(),
    );
    await archive.load();
    final note = await archive.save({
      'kind': 'note',
      'body': 'план учёбы',
      'title': 'Учебный план',
      'folder': 'Учёба',
    });
    await archive.save({
      'kind': 'credential',
      'title': 'РХТУ',
      'password': 'never-send-this',
    });
    final client = GeminiClient(
      ledger,
      apiKey: 'test',
      client: MockClient((r) async {
        expect(r.body, isNot(contains('never-send-this')));
        return http.Response(
          jsonEncode({
            'choices': [
              {
                'finish_reason': 'stop',
                'message': {
                  'content': jsonEncode({
                    'ids': [note.id, 'invented-id'],
                  }),
                },
              },
            ],
            'usage': {'prompt_tokens': 500, 'completion_tokens': 30},
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );
    expect(await client.search('когда каникулы', archive.records), [note.id]);
  });
  test(
    'master password minimum, random salts, and wrong-password rejection',
    () async {
      expect(
        () => PasswordEnvelope.encrypt({'password': 'test'}, 'short'),
        throwsA(isA<FormatException>()),
      );
      final value = await PasswordEnvelope.encrypt({
        'password': 'real-test-value',
      }, 'eight888');
      expect(jsonEncode(value), isNot(contains('real-test-value')));
      expect(
        (await PasswordEnvelope.decrypt(value, 'eight888'))['password'],
        'real-test-value',
      );
      await expectLater(
        () => PasswordEnvelope.decrypt(value, 'wrong888'),
        throwsA(anything),
      );
    },
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
