import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../core/controller.dart';
import '../core/setup_transfer.dart';
import 'widgets.dart';

class SetupScannerPage extends StatefulWidget {
  final BrainController brain;
  const SetupScannerPage({super.key, required this.brain});
  @override
  State<SetupScannerPage> createState() => _SetupScannerState();
}

class _SetupScannerState extends State<SetupScannerPage> {
  final camera = MobileScannerController(
    formats: [BarcodeFormat.qrCode],
    detectionSpeed: DetectionSpeed.normal,
  );
  bool busy = false;
  String status = 'Наведи камеру на личный код настройки Gemini';
  @override
  void dispose() {
    camera.dispose();
    super.dispose();
  }

  Future<void> detected(BarcodeCapture capture) async {
    if (busy) return;
    Map<String, dynamic>? setup;
    for (final barcode in capture.barcodes) {
      final raw = barcode.rawValue;
      if (raw == null) continue;
      try {
        setup = parseSetupCode(raw);
        break;
      } catch (_) {}
    }
    if (setup == null) {
      setState(
        () => status = 'Код не подходит. Открой «Подключить Gemini.command» и сканируй личный код Изbrainого.',
      );
      return;
    }
    setState(() => busy = true);
    try {
      await camera.stop();
    } catch (_) {}
    if (!mounted) return;
    final accepted = await confirm(
      context,
      setup['mode'] == 'settings_only'
          ? 'Подключить Gemini?'
          : 'Перенести с Mac?',
      setup['mode'] == 'settings_only'
          ? 'Будут сохранены только ключ Gemini, модель и лимит расходов. Файлы, заметки, папки и пароли из кода не загружаются.'
          : 'Будут подключены твой архив и Gemini из этого кода. Сканируй только свой код.',
    );
    if (!mounted) return;
    if (accepted) {
      final result = await Navigator.push<bool>(
        context,
        MaterialPageRoute<bool>(
          builder: (_) => SetupImportPage(brain: widget.brain, setup: setup!),
        ),
      );
      if (!mounted) return;
      if (result == true) {
        Navigator.pop(context, true);
        return;
      }
    }
    setState(() => busy = false);
    try {
      await camera.start();
    } catch (_) {
      if (mounted) {
        setState(
          () => status = 'Камера недоступна. Можно открыть изображение кода кнопкой сверху.',
        );
      }
    }
  }

  Future<void> fromImage() async {
    final picked = await FilePicker.pickFile();
    if (picked == null) return;
    final file = await copyPickedFile(picked, widget.brain.temporary);
    BarcodeCapture? result;
    try {
      result = await camera.analyzeImage(file.path);
    } finally {
      if (file.parent.path == widget.brain.temporary.path &&
          file.path.split('/').last.startsWith('picked-') &&
          await file.exists()) {
        await file.delete();
      }
    }
    if (result != null) {
      await detected(result);
    } else if (mounted) {
      notice(context, 'На изображении не найден код настройки.');
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Настройка по QR'),
      actions: [
        IconButton(
          tooltip: 'Открыть код из изображения',
          onPressed: busy ? null : () => safely(context, fromImage),
          icon: const Icon(Icons.image_outlined),
        ),
      ],
    ),
    body: SafeArea(
      child: Column(
        children: [
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(24),
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: MobileScanner(
                  controller: camera,
                  onDetect: detected,
                  tapToFocus: true,
                  errorBuilder: (_, _) => const Center(
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        'Не удалось открыть камеру. Разреши доступ к камере в настройках телефона или открой изображение кода кнопкой сверху.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (busy) const LinearProgressIndicator(minHeight: 2),
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 20, 24, 36),
            child: Text(
              status,
              textAlign: TextAlign.center,
              style: const TextStyle(color: muted, height: 1.5),
            ),
          ),
        ],
      ),
    ),
  );
}

/// Keep transfer feedback separate from the stopped camera preview.
class SetupImportPage extends StatefulWidget {
  final BrainController brain;
  final Map<String, dynamic> setup;
  const SetupImportPage({super.key, required this.brain, required this.setup});
  @override
  State<SetupImportPage> createState() => _SetupImportState();
}

class _SetupImportState extends State<SetupImportPage> {
  bool running = false, complete = false;
  String phase = '';
  double? progress;
  @override
  void initState() {
    super.initState();
    _transfer();
  }

  Future<void> _transfer() async {
    setState(() {
      running = true;
      progress = null;
      phase = widget.setup['mode'] == 'settings_only'
          ? 'Сохраняю настройки Gemini…'
          : 'Подключаюсь к Mac…';
    });
    try {
      await widget.brain.applySetup(
        widget.setup,
        onProgress: (text, value) {
          if (mounted) {
            setState(() {
              phase = text;
              progress = value;
            });
          }
        },
      );
      if (mounted) {
        setState(() {
          running = false;
          complete = true;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          running = false;
          phase = BrainController.friendly(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !running,
    child: Scaffold(
      appBar: AppBar(
        title: Text(
          widget.setup['mode'] == 'settings_only'
              ? 'Подключение Gemini'
              : 'Перенос с Mac',
        ),
        automaticallyImplyLeading: !running,
      ),
      body: Center(
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  complete
                      ? Icons.check_circle_outline
                      : running
                      ? Icons.sync
                      : Icons.wifi_off,
                  size: 64,
                  color: accent,
                ),
                const SizedBox(height: 24),
                Text(
                  complete
                      ? widget.setup['mode'] == 'settings_only'
                            ? 'Настройки сохранены'
                            : 'Архив перенесён'
                      : running
                      ? widget.setup['mode'] == 'settings_only'
                            ? 'Подключаю Gemini'
                            : 'Переношу твои данные'
                      : widget.setup['mode'] == 'settings_only'
                      ? 'Настройка не завершена'
                      : 'Перенос не завершён',
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 26,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 20),
                Text(
                  complete
                      ? widget.setup['mode'] == 'settings_only'
                            ? 'Gemini ${widget.brain.ai.enabled ? "подключён" : "не подключён: в коде нет API-ключа"}.\nСохранены только настройки. Старые материалы не загружались.'
                            : 'Материалов: ${widget.brain.materials.length}.\nGemini ${widget.brain.ai.enabled ? "подключён" : "не подключён: в коде нет API-ключа"}.\nГлавный пароль хранилища прежний.'
                      : phase,
                  textAlign: TextAlign.center,
                  style: const TextStyle(height: 1.6),
                ),
                const SizedBox(height: 24),
                if (running) ...[
                  LinearProgressIndicator(value: progress),
                  const SizedBox(height: 16),
                  const Text(
                    'Оставь приложение открытым до завершения.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: muted),
                  ),
                ] else if (complete)
                  FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Готово'),
                  )
                else ...[
                  FilledButton(
                    onPressed: _transfer,
                    child: Text(
                      widget.setup['mode'] == 'settings_only'
                          ? 'Повторить настройку'
                          : 'Повторить перенос',
                    ),
                  ),
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Сканировать новый код'),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    ),
  );
}
