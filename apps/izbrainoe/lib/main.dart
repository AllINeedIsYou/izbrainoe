import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'core/controller.dart';
import 'ui/home.dart';
import 'ui/widgets.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  BrainController? controller;
  String? failure;
  try {
    final support = await getApplicationSupportDirectory(),
        cache = await getTemporaryDirectory();
    controller = BrainController(
      Directory(p.join(support.path, 'archive-v1')),
      Directory(p.join(cache.path, 'originals-v1')),
      DeviceSecrets(),
    );
    await controller.initialize();
  } catch (e) {
    failure = BrainController.friendly(e);
  }
  runApp(
    BrainApp(controller: failure == null ? controller : null, failure: failure),
  );
}

class BrainApp extends StatelessWidget {
  final BrainController? controller;
  final String? failure;
  const BrainApp({super.key, this.controller, this.failure});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Изbrainое',
    debugShowCheckedModeBanner: false,
    locale: const Locale('ru'),
    supportedLocales: const [Locale('ru'), Locale('en')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: brainTheme(),
    home: controller != null
        ? HomeScreen(brain: controller!)
        : Scaffold(
            body: SafeArea(
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.lock_outline, size: 42, color: accent),
                      const SizedBox(height: 20),
                      const Text(
                        'Архив не открылся',
                        style: TextStyle(fontSize: 24),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        failure ?? 'Перезапусти приложение.',
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 12),
                      const Text(
                        'Данные не заменены и не удалены.',
                        style: TextStyle(color: muted),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
  );
}
