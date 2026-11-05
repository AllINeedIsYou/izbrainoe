import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:izbrainoe/core/controller.dart';
import 'package:izbrainoe/core/archive_store.dart';
import 'package:izbrainoe/ui/chat_bubble.dart';
import 'package:izbrainoe/ui/widgets.dart';

class MemorySecrets extends SecretStore {
  final values = <String, String>{};
  @override
  Future<String?> read(String name) async => values[name];
  @override
  Future<void> write(String name, String value) async {
    values[name] = value;
  }
}

class DelayedBrain extends BrainController {
  final frame = Completer<Uint8List?>();
  int previewCalls = 0;
  DelayedBrain(super.root, super.temporary, super.secrets);
  @override
  Future<Uint8List?> preview(ArchiveRecord record) {
    previewCalls++;
    return frame.future;
  }
}

void main() {
  testWidgets(
    'ordinary left swipe asks to delete; vertical swipe scrolls without a dialog',
    (tester) async {
      final temp = await tester.runAsync(
        () => Directory.systemTemp.createTemp('izb-swipe-'),
      );
      final brain = BrainController(
        Directory('${temp!.path}/root'),
        Directory('${temp.path}/cache'),
        MemorySecrets(),
      );
      final records = await tester.runAsync(() async {
        await brain.initialize();
        return Future.wait(
          List.generate(
            18,
            (i) => brain.archive.save({
              'kind': 'note',
              'body': 'Запись $i',
              'title': 'Запись $i',
              'folder': 'Заметки',
            }),
          ),
        );
      });
      final scroll = ScrollController();
      await tester.binding.setSurfaceSize(const Size(360, 760));
      await tester.pumpWidget(
        MaterialApp(
          theme: brainTheme(),
          home: Scaffold(
            body: ListView(
              controller: scroll,
              children: [
                for (final r in records!)
                  ChatBubble(
                    key: ValueKey(r.id),
                    brain: brain,
                    record: r,
                    onTap: () {},
                  ),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.drag(find.text('Запись 0'), const Offset(-110, 0));
      await tester.pumpAndSettle();
      expect(find.text('Удалить сообщение?'), findsOneWidget);
      await tester.tap(find.text('Нет'));
      await tester.pumpAndSettle();
      expect(brain.materials.length, 18);
      await tester.drag(find.text('Запись 0'), const Offset(0, -180));
      await tester.pumpAndSettle();
      expect(scroll.offset, greaterThan(0));
      expect(find.text('Удалить сообщение?'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      scroll.dispose();
      brain.dispose();
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );
  testWidgets(
    'photo row keeps exactly the same size while preview loads and metadata rebuilds',
    (tester) async {
      final temp = await tester.runAsync(
        () => Directory.systemTemp.createTemp('izb-photo-layout-'),
      );
      final brain = DelayedBrain(
        Directory('${temp!.path}/root'),
        Directory('${temp.path}/cache'),
        MemorySecrets(),
      );
      final record = await tester.runAsync(() async {
        await brain.initialize();
        return brain.archive.save({
          'kind': 'photo',
          'title': 'Фото.jpg',
          'filename': 'Фото.jpg',
          'photo_aspect': .5,
          'folder': 'Фото',
          'blob': {'hash': 'a' * 64, 'size': 3, 'chunks': 1},
        });
      });
      await tester.binding.setSurfaceSize(const Size(360, 760));
      await tester.pumpWidget(
        MaterialApp(
          theme: brainTheme(),
          home: Scaffold(
            body: AnimatedBuilder(
              animation: brain,
              builder: (_, _) => ListView(
                children: [
                  ChatBubble(
                    key: ValueKey(record!.id),
                    brain: brain,
                    record: record,
                    onTap: () {},
                  ),
                ],
              ),
            ),
          ),
        ),
      );
      await tester.pump();
      final material = find
          .descendant(
            of: find.byType(ChatBubble),
            matching: find.byType(Material),
          )
          .first;
      final before = tester.getSize(material);
      brain.frame.complete(img.encodePng(img.Image(width: 100, height: 200)));
      await tester.pumpAndSettle();
      expect(tester.getSize(material), before);
      brain.changed();
      await tester.pumpAndSettle();
      expect(tester.getSize(material), before);
      expect(brain.previewCalls, 1);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      brain.dispose();
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );
}
