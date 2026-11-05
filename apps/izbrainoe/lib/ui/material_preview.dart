import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';

class RecordPreview extends StatelessWidget {
  final BrainController brain;
  final ArchiveRecord record;
  final BoxFit fit;
  const RecordPreview({
    super.key,
    required this.brain,
    required this.record,
    this.fit = BoxFit.cover,
  });
  @override
  Widget build(BuildContext context) => FutureBuilder<Uint8List?>(
    future: brain.preview(record),
    builder: (context, snapshot) {
      if (snapshot.data != null) {
        return Stack(
          fit: StackFit.expand,
          children: [
            Image.memory(
              snapshot.data!,
              fit: record.kind == 'photo' || record.kind == 'video'
                  ? fit
                  : BoxFit.contain,
              gaplessPlayback: true,
              errorBuilder: (_, _, _) => _fallback(context),
            ),
            if (record.kind == 'video')
              const Center(
                child: Icon(
                  Icons.play_circle_fill,
                  size: 38,
                  color: Colors.white,
                ),
              ),
          ],
        );
      }
      return _fallback(context);
    },
  );
  Widget _fallback(BuildContext context) {
    final text =
        (record.data['body'] as String? ??
                record.data['text'] as String? ??
                record.data['summary'] as String? ??
                '')
            .trim();
    final icon = switch (record.kind) {
      'photo' => Icons.image_outlined,
      'video' => Icons.play_circle_outline,
      'audio' => Icons.music_note,
      'link' => Icons.link,
      'note' => Icons.notes,
      _ => Icons.description_outlined,
    };
    return ColoredBox(
      color: Theme.of(context).colorScheme.surfaceContainerHigh,
      child: LayoutBuilder(
        builder: (context, size) {
          if (size.maxWidth < 90 || size.maxHeight < 80) {
            return Center(child: Icon(icon, size: 26));
          }
          return Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 24),
                const SizedBox(height: 10),
                if (text.isNotEmpty)
                  Expanded(
                    child: Text(
                      text,
                      maxLines: 8,
                      overflow: TextOverflow.fade,
                      style: const TextStyle(fontSize: 11, height: 1.4),
                    ),
                  )
                else
                  Expanded(
                    child: Center(
                      child: Text(
                        record.kind == 'video'
                            ? 'Видео'
                            : record.kind == 'audio'
                            ? 'Аудио'
                            : record.kind == 'photo'
                            ? 'Фото'
                            : record.title,
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 13),
                      ),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class FolderSymbol extends StatelessWidget {
  final String folder;
  final Color? color;
  const FolderSymbol(this.folder, {super.key, this.color});
  @override
  Widget build(BuildContext context) {
    final s = folder.toLowerCase();
    final IconData? icon = switch (s) {
      _ when RegExp(r'математ|вышмат|матан').hasMatch(s) => Icons.functions,
      _ when RegExp(r'^it$|програм|код|проект|разработ').hasMatch(s) =>
        Icons.code,
      _ when RegExp(r'музык|аудио|песн|трек').hasMatch(s) =>
        Icons.music_note_outlined,
      _ when RegExp(r'видео|фильм|кино').hasMatch(s) => Icons.movie_outlined,
      _ when RegExp(r'документ|договор|чек|справ').hasMatch(s) =>
        Icons.description_outlined,
      _ when RegExp(r'англий|язык|француз|немец').hasMatch(s) =>
        Icons.translate,
      _ when RegExp(r'учеб|учёб|вуз|универ|курс').hasMatch(s) =>
        Icons.school_outlined,
      _ when RegExp(r'рецепт|ресторан|еда|кафе|готов').hasMatch(s) =>
        Icons.restaurant_outlined,
      _ when RegExp(r'путеше|поезд|отпуск').hasMatch(s) =>
        Icons.flight_outlined,
      _ when RegExp(r'фото|картин').hasMatch(s) => Icons.photo_camera_outlined,
      _ when RegExp(r'книг|чтени').hasMatch(s) => Icons.menu_book_outlined,
      _ when RegExp(r'zip|архив').hasMatch(s) => Icons.inventory_2_outlined,
      _ when RegExp(r'дом|ремонт').hasMatch(s) => Icons.home_outlined,
      _ when RegExp(r'финанс|деньг|банк').hasMatch(s) =>
        Icons.account_balance_wallet_outlined,
      _ when RegExp(r'здоров|медицин').hasMatch(s) =>
        Icons.health_and_safety_outlined,
      _ when RegExp(r'замет|иде').hasMatch(s) => Icons.lightbulb_outline,
      _ => null,
    };
    return SizedBox(
      width: 24,
      height: 24,
      child: Center(
        child: icon != null
            ? Icon(icon, size: 24, color: color)
            : Text(
                '<3',
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 16,
                  height: 1,
                  fontWeight: FontWeight.w600,
                  letterSpacing: -1,
                  color: color,
                ),
              ),
      ),
    );
  }
}
