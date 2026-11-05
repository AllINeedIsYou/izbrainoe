import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/controller.dart';
import 'package:izbrainoe/core/crypto_box.dart';
import 'package:izbrainoe/ui/setup_scanner.dart';
import 'package:izbrainoe/ui/widgets.dart';

class MemorySecrets extends SecretStore {
  final values = <String, String>{};
  @override
  Future<String?> read(String name) async => values[name];
  @override
  Future<void> write(String name, String value) async => values[name] = value;
}

class ControlledTransfer extends BrainController {
  final steps = <Completer<void>>[];
  void Function(String, double?)? report;
  ControlledTransfer(Directory root, Directory cache)
    : super(root, cache, MemorySecrets());
  @override
  Future<void> applySetup(
    Map<String, dynamic> setup, {
    void Function(String phase, double? progress)? onProgress,
  }) async {
    report = onProgress;
    final step = Completer<void>();
    steps.add(step);
    await step.future;
    ai.apiKey = 'synthetic-test-key';
  }
}

void main() {
  testWidgets(
    'transfer shows progress, error, retry and explicit connected result',
    (tester) async {
      final temp = await tester.runAsync(
        () => Directory.systemTemp.createTemp('izb-transfer-ui-'),
      );
      final brain = ControlledTransfer(
        Directory('${temp!.path}/root'),
        Directory('${temp.path}/tmp'),
      );
      await tester.runAsync(brain.initialize);
      await tester.pumpWidget(
        MaterialApp(
          theme: brainTheme(),
          home: SetupImportPage(brain: brain, setup: const {}),
        ),
      );
      await tester.pump();
      expect(find.text('Переношу твои данные'), findsOneWidget);
      expect(find.byType(LinearProgressIndicator), findsOneWidget);
      brain.report!('Получаю архив с Mac: 5.0 МБ из 20.0 МБ', .25);
      await tester.pump();
      expect(
        find.text('Получаю архив с Mac: 5.0 МБ из 20.0 МБ'),
        findsOneWidget,
      );
      expect(
        tester
            .widget<LinearProgressIndicator>(
              find.byType(LinearProgressIndicator),
            )
            .value,
        .25,
      );
      brain.steps.first.completeError(
        const FormatException('Mac недоступен — проверь Wi-Fi.'),
      );
      await tester.pump();
      expect(find.text('Перенос не завершён'), findsOneWidget);
      expect(find.text('Mac недоступен — проверь Wi-Fi.'), findsOneWidget);
      await tester.tap(find.text('Повторить перенос'));
      await tester.pump();
      expect(brain.steps.length, 2);
      brain.steps.last.complete();
      await tester.pumpAndSettle();
      expect(find.text('Архив перенесён'), findsOneWidget);
      expect(find.textContaining('Gemini подключён.'), findsOneWidget);
      expect(find.text('Готово'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      brain.dispose();
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );

  test(
    'failed download leaves archive identity and stored API key intact',
    () async {
      final temp = await Directory.systemTemp.createTemp(
        'izb-transfer-failure-',
      );
      final secrets = MemorySecrets();
      final brain = BrainController(
        Directory('${temp.path}/root'),
        Directory('${temp.path}/tmp'),
        secrets,
      );
      try {
        await brain.initialize();
        final before = brain.archiveCode;
        await secrets.write('provod_key', 'existing-test-key');
        await expectLater(
          brain.applySetup({
            'archive_code': ArchiveCipher.createCode(),
            'provod_key': 'replacement-test-key',
            'bundle_url': 'http://127.0.0.1/invalid',
          }),
          throwsFormatException,
        );
        expect(brain.archiveCode, before);
        expect(await secrets.read('archive_code'), before);
        expect(await secrets.read('provod_key'), 'existing-test-key');
      } finally {
        brain.dispose();
        await temp.delete(recursive: true);
      }
    },
  );
}
