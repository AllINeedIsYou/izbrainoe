import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:file_picker/file_picker.dart';

import '../core/controller.dart';
import 'widgets.dart';

Future<int> addAttachments(
  BuildContext context,
  BrainController brain, {
  String? folder,
}) async {
  final gallery = await showModalBottomSheet<bool>(
    context: context,
    showDragHandle: true,
    builder: (c) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: const Text('Галерея'),
            subtitle: const Text('Фото и видео'),
            onTap: () => Navigator.pop(c, true),
          ),
          ListTile(
            leading: const Icon(Icons.folder_open_outlined),
            title: const Text('Проводник'),
            subtitle: const Text('Любые файлы'),
            onTap: () => Navigator.pop(c, false),
          ),
          const SizedBox(height: 12),
        ],
      ),
    ),
  );
  if (gallery == null) return 0;
  final picked = <({File file, String name, bool cleanup})>[];
  if (gallery && Platform.isAndroid) {
    final files = await const MethodChannel('app.izbrainoe/media')
        .invokeListMethod<dynamic>('pickGallery');
    for (final f in files ?? []) {
      final m = f as Map;
      picked.add((
        file: File(m['path'] as String),
        name: m['name'] as String,
        cleanup: true,
      ));
    }
  } else {
    for (final f in await FilePicker.pickFiles(
      type: gallery ? FileType.media : FileType.any,
    )) {
      final source = await copyPickedFile(f, brain.temporary);
      picked.add((
        file: source,
        name: f.name,
        cleanup:
            source.parent.path == brain.temporary.path &&
            source.uri.pathSegments.last.startsWith('picked-'),
      ));
    }
  }
  var saved = 0;
  for (final pick in picked) {
    try {
      await brain.addFile(pick.file, name: pick.name, folder: folder);
      saved++;
    } finally {
      if (pick.cleanup && await pick.file.exists()) await pick.file.delete();
    }
  }
  return saved;
}
