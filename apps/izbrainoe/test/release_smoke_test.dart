import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/controller.dart';
import 'package:izbrainoe/core/intelligence.dart';
import 'package:izbrainoe/core/archive_store.dart';
import 'package:izbrainoe/main.dart';
import 'package:izbrainoe/ui/chat_bubble.dart';

class MemorySecrets extends SecretStore {
  final values = <String, String>{};
  @override
  Future<String?> read(String name) async => values[name];
  @override
  Future<void> write(String name, String value) async {
    values[name] = value;
  }
}

class RoutingAi extends GeminiClient {
  int analyses = 0, matches = 0;
  RoutingAi(super.usage) : super(apiKey: 'synthetic-only');
  @override
  Future<Map<String, dynamic>> analyze(
    Map<String, dynamic> item,
    PreparedFile prepared,
    List<String> folders,
    List<Map<String, dynamic>> examples, {
    List<Map<String, dynamic>> rules = const [],
  }) async {
    analyses++;
    expect(rules.first['folder'], 'Рестораны');
    return {
      'folder': 'Рестораны',
      'summary': 'Меню ресторана',
      'recognized_text': '',
      'topics': ['ресторан'],
      'document_type': 'меню',
      'confidence': .99,
      'needs_context': false,
    };
  }

  @override
  Future<List<String>> matchFolderRule(
    String description,
    List<ArchiveRecord> records,
  ) async {
    matches++;
    return records
        .where(
          (r) => '${r.data['body']} ${r.data['text']}'.contains('ресторана'),
        )
        .map((r) => r.id)
        .toList();
  }
}

Future<void> settle(BrainController brain) async {
  for (var i = 0; i < 300 && brain.pending > 0; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(brain.pending, 0);
}

void main() {
  test('new semantic rule overrides old format rule, scan is optional, video is free, clear removes archive and keys', () async {
    final temp = await Directory.systemTemp.createTemp('izb-release-');
    final secrets = MemorySecrets();
    final brain = BrainController(
      Directory('${temp.path}/root'),
      Directory('${temp.path}/cache'),
      secrets,
    );
    try {
      await brain.initialize();
      brain.ai = RoutingAi(brain.usage);
      final ai = brain.ai as RoutingAi;
      await brain.createFolderRule('Документы', 'txt');
      final old = await brain.archive.save({
        'kind': 'note',
        'title': 'Меню',
        'body': 'Меню ресторана',
        'folder': 'Документы',
        'manual_folder': true,
        'analysis_state': 'done',
      });
      await brain.createFolderRule(
        'Рестораны',
        'Всё, что связано с ресторанами',
      );
      expect(brain.archive.get(old.id)!.folder, 'Документы');
      expect(ai.matches, 0);
      await brain.createFolderRule(
        'Рестораны',
        'Всё, что связано с ресторанами',
        reviewExisting: true,
      );
      expect(brain.archive.get(old.id)!.folder, 'Рестораны');
      expect(ai.matches, 1);
      final text = File('${temp.path}/menu.txt');
      await text.writeAsString('Меню ресторана');
      final incoming = await brain.addFile(text);
      await settle(brain);
      expect(brain.archive.get(incoming.id)!.folder, 'Рестораны');
      expect(ai.analyses, 1);
      final video = File('${temp.path}/film.mp4');
      await video.writeAsBytes([1, 2, 3]);
      final r = await brain.addFile(video);
      await settle(brain);
      expect(brain.archive.get(r.id)!.folder, 'Видео');
      expect(ai.analyses, 1);
      await brain.deleteFolder('Рестораны');
      expect(brain.materials.any((r) => r.folder == 'Рестораны'), false);
      expect(brain.trash.length, 2);
      await brain.preferences({
        'pinned_folders': ['Видео'],
      });
      await secrets.write('provod_key', 'synthetic-key');
      final directory = brain.archive.directory, oldCode = brain.archiveCode;
      await brain.clearAll();
      expect(await directory.exists(), false);
      expect(brain.archive.records, isEmpty);
      expect(brain.archiveCode, isNot(oldCode));
      expect(await secrets.read('provod_key'), '');
      expect(brain.ai.enabled, false);
    } finally {
      brain.dispose();
      await temp.delete(recursive: true);
    }
  });
  testWidgets(
    'short message shrinks to content and attachment menu offers gallery and files',
    (tester) async {
      final temp = await tester.runAsync(
        () => Directory.systemTemp.createTemp('izb-bubble-'),
      );
      final brain = BrainController(
        Directory('${temp!.path}/root'),
        Directory('${temp.path}/cache'),
        MemorySecrets(),
      );
      await tester.runAsync(() async {
        await brain.initialize();
        await brain.addNote('п');
        await settle(brain);
      });
      await tester.binding.setSurfaceSize(const Size(360, 800));
      await tester.pumpWidget(BrainApp(controller: brain));
      await tester.pumpAndSettle();
      final bubble = find.byType(ChatBubble);
      expect(bubble, findsOneWidget);
      final material = find
          .descendant(of: bubble, matching: find.byType(Material))
          .first;
      expect(tester.getSize(material).width, lessThan(110));
      await tester.tap(find.byTooltip('Прикрепить файл'));
      await tester.pumpAndSettle();
      expect(find.text('Галерея'), findsOneWidget);
      expect(find.text('Проводник'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      brain.dispose();
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );
}
