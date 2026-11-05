import 'dart:io';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/controller.dart';
import 'package:izbrainoe/main.dart';

class MemorySecrets extends SecretStore {
  final data = <String, String>{};
  @override
  Future<String?> read(String name) async => data[name];
  @override
  Future<void> write(String name, String value) async {
    data[name] = value;
  }
}

void main() {
  testWidgets(
    'chat has only saved material; search and locked vault are separate',
    (tester) async {
      final temp = await tester.runAsync(
        () => Directory.systemTemp.createTemp('izb-widget-'),
      );
      final brain = BrainController(
        Directory('${temp!.path}/root'),
        Directory('${temp.path}/tmp'),
        MemorySecrets(),
      );
      await tester.runAsync(() async {
        await brain.initialize();
        await brain.addNote('Моя важная мысль');
        for (var i = 0; i < 100 && brain.pending > 0; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 10));
        }
      });
      await tester.binding.setSurfaceSize(const Size(360, 780));
      final shakes = StreamController<void>.broadcast(sync: true);
      await tester.pumpWidget(
        BrainApp(controller: brain, shakes: shakes.stream),
      );
      await tester.pumpAndSettle();
      expect(find.text('Моя важная мысль'), findsOneWidget);
      expect(find.text('Что найти?'), findsNothing);
      shakes.add(null);
      await tester.pumpAndSettle();
      expect(find.text('КОЛЛЕКЦИИ'), findsOneWidget);
      await tester.tap(find.text('Заметки').first);
      await tester.pumpAndSettle();
      shakes.add(null);
      await tester.pumpAndSettle();
      expect(find.text('КОЛЛЕКЦИИ'), findsNothing);
      expect(find.byTooltip('Прикрепить файл'), findsOneWidget);
      await tester.tap(find.text('Моя важная мысль'));
      await tester.pumpAndSettle();
      expect(find.text('Твоё пояснение'), findsOneWidget);
      await tester.tap(find.byTooltip('Назад'));
      await tester.pumpAndSettle();
      expect(find.byType(NavigationBar), findsNothing);
      expect(find.byTooltip('Прикрепить файл'), findsOneWidget);
      await tester.tap(find.byTooltip('Разделы'));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Чат'), findsOneWidget);
      expect(find.byTooltip('Поиск'), findsOneWidget);
      expect(find.byTooltip('Подборки'), findsOneWidget);
      expect(find.byTooltip('Пароли'), findsOneWidget);
      await tester.tap(find.byTooltip('Поиск'));
      await tester.pumpAndSettle();
      expect(find.text('Что найти?'), findsOneWidget);
      await tester.tap(find.byTooltip('Разделы'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Подборки'));
      await tester.pumpAndSettle();
      expect(find.text('КОЛЛЕКЦИИ'), findsOneWidget);
      await tester.tap(find.byTooltip('Разделы'));
      await tester.pumpAndSettle();
      await tester.tapAt(const Offset(310, 600));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Чат'), findsNothing);
      await tester.tap(find.byTooltip('Разделы'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Пароли'));
      await tester.pumpAndSettle();
      expect(find.text('Пароли — под замком'), findsOneWidget);
      expect(find.text('Главный пароль'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.runAsync(() => brain.unlockVault('eight888'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Добавить пароль'));
      await tester.pumpAndSettle();
      expect(find.text('Новый пароль'), findsOneWidget);
      brain.lockVault();
      await tester.pumpAndSettle();
      expect(find.text('Новый пароль'), findsNothing);
      expect(find.text('Пароли — под замком'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      brain.dispose();
      await shakes.close();
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );
}
