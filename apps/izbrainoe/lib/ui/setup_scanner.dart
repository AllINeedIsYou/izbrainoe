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
    detectionSpeed: DetectionSpeed.noDuplicates,
  );
  bool busy = false;
  String status = 'Наведи камеру на код настройки на Mac';
  @override
  void dispose() {
    camera.dispose();
    super.dispose();
  }

  Future<void> detected(BarcodeCapture capture) async {
    if (busy) return;
    final raw = capture.barcodes.firstOrNull?.rawValue;
    if (raw == null) return;
    Map<String, dynamic> setup;
    try {
      setup = parseSetupCode(raw);
    } catch (_) {
      return;
    }
    setState(() => busy = true);
    await camera.stop();
    if (!mounted) return;
    if (!await confirm(
      context,
      'Перенести с Mac?',
      'Будут подключены твой архив и Gemini из этого кода. Сканируй только код, открытый на твоём Mac.',
    )) {
      if (mounted) {
        setState(() => busy = false);
        await camera.start();
      }
      return;
    }
    try {
      setState(() => status = 'Переношу архив и настройки…');
      await widget.brain.applySetup(setup);
      if (mounted) {
        notice(context, 'Архив и настройки перенесены.');
        Navigator.pop(context);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          status = BrainController.friendly(e);
          busy = false;
        });
        await camera.start();
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
      title: const Text('Перенести с Mac'),
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
                child: MobileScanner(controller: camera, onDetect: detected),
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
