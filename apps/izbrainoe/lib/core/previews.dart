import 'dart:io';
import 'dart:math';

import 'package:archive/archive.dart';
import 'package:flutter/services.dart';
import 'package:path/path.dart' as p;
import 'package:pdfx/pdfx.dart';

import 'intelligence.dart';

/// Local thumbnails never call Gemini or upload the original.
Future<Uint8List?> localThumbnail(File file, String filename) async {
  final kind = kindFor(filename), ext = p.extension(filename).toLowerCase();
  if (kind == 'photo') {
    return (await prepareFile(file, filename)).images.firstOrNull;
  }
  if (ext == '.pdf') {
    PdfDocument? document;
    PdfPage? page;
    try {
      document = await PdfDocument.openFile(file.path);
      if (document.pagesCount < 1) return null;
      page = await document.getPage(1);
      final scale = 640 / max(page.width, page.height);
      return (await page.render(
        width: page.width * scale,
        height: page.height * scale,
        format: PdfPageImageFormat.jpeg,
        quality: 75,
        backgroundColor: '#FFFFFF',
      ))?.bytes;
    } finally {
      await page?.close();
      await document?.close();
    }
  }
  if (['video', 'audio'].contains(kind) && Platform.isAndroid) {
    return const MethodChannel('app.izbrainoe/media').invokeMethod<Uint8List>(
      'previewMedia',
      {'path': file.path, 'video': kind == 'video'},
    );
  }
  if ([
        '.docx',
        '.xlsx',
        '.pptx',
        '.odt',
        '.ods',
        '.odp',
        '.epub',
      ].contains(ext) &&
      await file.length() <= 32 * 1024 * 1024) {
    final zip = ZipDecoder().decodeBytes(await file.readAsBytes());
    for (final entry in zip.files) {
      if (entry.isFile &&
          entry.size < 2 * 1024 * 1024 &&
          RegExp(
            r'(thumbnail|cover).*\.(png|jpe?g)$',
            caseSensitive: false,
          ).hasMatch(entry.name)) {
        return entry.readBytes();
      }
    }
  }
  return null;
}
