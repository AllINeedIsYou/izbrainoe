import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';

const background = Color(0xff10151e),
    surface = Color(0xff1a2230),
    accent = Color(0xffc0f58c),
    muted = Color(0xff9caaa5),
    line = Color(0xff303d47);
ThemeData brainTheme() => ThemeData(
  brightness: Brightness.dark,
  useMaterial3: true,
  scaffoldBackgroundColor: background,
  colorScheme: const ColorScheme.dark(
    primary: accent,
    onPrimary: Color(0xff203017),
    surface: background,
    onSurface: Color(0xfff0f3ed),
    secondary: accent,
    error: Color(0xffffb4ab),
  ),
  appBarTheme: const AppBarTheme(
    backgroundColor: background,
    scrolledUnderElevation: 0,
    centerTitle: false,
  ),
  snackBarTheme: SnackBarThemeData(
    backgroundColor: surface,
    contentTextStyle: const TextStyle(color: Color(0xfff0f3ed)),
    behavior: SnackBarBehavior.floating,
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
  ),
  inputDecorationTheme: InputDecorationTheme(
    filled: true,
    fillColor: surface,
    contentPadding: const EdgeInsets.all(16),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: line),
    ),
    enabledBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: line),
    ),
    focusedBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: const BorderSide(color: accent),
    ),
    hintStyle: const TextStyle(color: muted),
  ),
  navigationBarTheme: NavigationBarThemeData(
    backgroundColor: background,
    indicatorColor: const Color(0xff263626),
    height: 74,
    labelTextStyle: WidgetStateProperty.resolveWith(
      (s) => TextStyle(
        fontSize: 11,
        color: s.contains(WidgetState.selected) ? accent : muted,
      ),
    ),
  ),
  filledButtonTheme: FilledButtonThemeData(
    style: FilledButton.styleFrom(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 15),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
  ),
  outlinedButtonTheme: OutlinedButtonThemeData(
    style: OutlinedButton.styleFrom(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      side: const BorderSide(color: line),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
  ),
  dividerTheme: const DividerThemeData(color: line, space: 24),
);

void notice(BuildContext context, String message) {
  final messenger = ScaffoldMessenger.of(context);
  messenger.hideCurrentSnackBar();
  messenger.showSnackBar(
    SnackBar(content: Text(message), duration: const Duration(seconds: 4)),
  );
}

Future<T?> safely<T>(BuildContext context, Future<T> Function() action) async {
  try {
    return await action();
  } catch (e) {
    if (context.mounted) notice(context, BrainController.friendly(e));
    return null;
  }
}

Future<bool> confirm(BuildContext context, String title, String text) =>
    showBrainDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: Text(text),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Продолжить'),
          ),
        ],
      ),
    ).then((v) => v ?? false);
IconData fileIcon(String kind) => switch (kind) {
  'photo' => Icons.image_outlined,
  'audio' => Icons.music_note_outlined,
  'video' => Icons.videocam_outlined,
  'link' => Icons.link,
  'note' => Icons.notes_rounded,
  _ => Icons.description_outlined,
};
String sizeLabel(int bytes) => bytes < 1024
    ? '$bytes Б'
    : bytes < 1024 * 1024
    ? '${(bytes / 1024).toStringAsFixed(0)} КБ'
    : bytes < 1024 * 1024 * 1024
    ? '${(bytes / 1024 / 1024).toStringAsFixed(1)} МБ'
    : '${(bytes / 1024 / 1024 / 1024).toStringAsFixed(1)} ГБ';
String dayLabel(int milliseconds) {
  final d = DateTime.fromMillisecondsSinceEpoch(milliseconds).toLocal(),
      now = DateTime.now();
  final a = DateTime(d.year, d.month, d.day),
      b = DateTime(now.year, now.month, now.day);
  if (a == b) return 'Сегодня';
  if (b.difference(a).inDays == 1) return 'Вчера';
  const months = [
    'января',
    'февраля',
    'марта',
    'апреля',
    'мая',
    'июня',
    'июля',
    'августа',
    'сентября',
    'октября',
    'ноября',
    'декабря',
  ];
  return '${d.day} ${months[d.month - 1]}${d.year != now.year ? ' ${d.year}' : ''}';
}

String timeLabel(int value) {
  final t = DateTime.fromMillisecondsSinceEpoch(value).toLocal();
  return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

class SurfaceBox extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  const SurfaceBox({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(18),
  });
  @override
  Widget build(BuildContext context) => Material(
    color: surface,
    clipBehavior: Clip.antiAlias,
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(20),
      side: const BorderSide(color: line),
    ),
    child: Padding(padding: padding, child: child),
  );
}

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title, message;
  final Widget? action;
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });
  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(28),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: surface,
              borderRadius: BorderRadius.circular(22),
            ),
            child: Icon(icon, color: accent, size: 34),
          ),
          const SizedBox(height: 22),
          Text(
            title,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w500),
          ),
          const SizedBox(height: 10),
          Text(
            message,
            textAlign: TextAlign.center,
            style: const TextStyle(color: muted, height: 1.5),
          ),
          if (action != null) ...[const SizedBox(height: 20), action!],
        ],
      ),
    ),
  );
}

class MaterialTile extends StatelessWidget {
  final ArchiveRecord record;
  final VoidCallback onTap;
  final Widget? trailing;
  const MaterialTile({
    super.key,
    required this.record,
    required this.onTap,
    this.trailing,
  });
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 9),
    child: Material(
      color: surface,
      borderRadius: BorderRadius.circular(16),
      child: ListTile(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
        leading: Container(
          padding: const EdgeInsets.all(11),
          decoration: BoxDecoration(
            color: const Color(0xff263626),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(fileIcon(record.kind), color: accent),
        ),
        title: Text(record.title, maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          '${record.folder}${record.conflict ? ' · есть две версии' : ''}',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(color: muted, fontSize: 12),
        ),
        trailing: trailing ?? const Icon(Icons.chevron_right, color: muted),
        onTap: onTap,
      ),
    ),
  );
}

Future<void> copySecret(BuildContext context, String value) async {
  if (Platform.isAndroid) {
    await const MethodChannel('app.izbrainoe/security')
        .invokeMethod<void>('copySecret', {'text': value});
  } else {
    await Clipboard.setData(ClipboardData(text: value));
  }
  if (context.mounted) {
    notice(context, 'Скопировано. Буфер очистится через 45 секунд.');
  }
  Timer(const Duration(seconds: 45), () async {
    final current = await Clipboard.getData(Clipboard.kTextPlain);
    if (current?.text == value) {
      await Clipboard.setData(const ClipboardData(text: ''));
    }
  });
}

Future<File> copyPickedFile(PlatformFile picked, Directory temporary) async {
  final path = picked.path;
  if (path != null && await File(path).exists()) return File(path);
  await temporary.create(recursive: true);
  final file = File(
    '${temporary.path}/picked-${DateTime.now().microsecondsSinceEpoch}',
  );
  final writer = file.openWrite();
  var bytes = 0;
  try {
    await for (final block in picked.readAsByteStream()) {
      bytes += block.length;
      if (bytes > 2 * 1024 * 1024 * 1024) {
        throw const FormatException('Файл больше 2 ГБ.');
      }
      writer.add(block);
    }
    await writer.flush();
    await writer.close();
    return file;
  } catch (_) {
    await writer.close();
    if (await file.exists()) await file.delete();
    rethrow;
  }
}

// Wait until the route has released its fields before disposing their controllers.
Future<T?> showBrainDialog<T>({
  required BuildContext context,
  required WidgetBuilder builder,
  bool barrierDismissible = true,
}) async {
  final navigator = Navigator.of(context, rootNavigator: true);
  final route = DialogRoute<T>(
    context: context,
    builder: builder,
    themes: InheritedTheme.capture(from: context, to: navigator.context),
    barrierDismissible: barrierDismissible,
    animationStyle: AnimationStyle.noAnimation,
  );
  final result = await navigator.push<T>(route);
  await route.completed;
  return result;
}
