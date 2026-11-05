import 'dart:io';

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
      await tester.pumpWidget(BrainApp(controller: brain));
      await tester.pumpAndSettle();
      expect(find.text('Моя важная мысль'), findsOneWidget);
      expect(find.text('Что найти?'), findsNothing);
      await tester.tap(find.text('Поиск'));
      await tester.pumpAndSettle();
      expect(find.text('Найти в архиве'), findsOneWidget);
      await tester.tap(find.byTooltip('Назад'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Пароли'));
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
      await tester.runAsync(() => temp.delete(recursive: true));
    },
  );
}
