import 'dart:io';

import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';
import 'package:pdfx/pdfx.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import 'widgets.dart';

Future<void> editContext(
  BuildContext context,
  BrainController brain,
  ArchiveRecord record,
) async {
  final text = TextEditingController(
        text: record.data['context'] as String? ?? '',
      ),
      folder = TextEditingController(
        text: record.data['manual_folder'] == true ? record.folder : '',
      );
  var busy = false;
  await showBrainDialog<void>(
    context: context,
    builder: (c) => StatefulBuilder(
      builder: (c, set) => AlertDialog(
        title: const Text('Пояснение'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                record.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: muted, fontSize: 13),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: text,
                autofocus: true,
                minLines: 3,
                maxLines: 7,
                decoration: const InputDecoration(
                  labelText: 'Для чего тебе этот материал?',
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: folder,
                decoration: const InputDecoration(
                  labelText: 'Подборка вручную',
                  hintText: 'Оставь пустой для автосортировки',
                ),
              ),
              if (brain.folders.isNotEmpty) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 6,
                  runSpacing: 2,
                  children: brain.folders
                      .take(6)
                      .map(
                        (f) => ActionChip(
                          label: Text(f, style: const TextStyle(fontSize: 11)),
                          onPressed: () => folder.text = f,
                        ),
                      )
                      .toList(),
                ),
              ],
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(c),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: busy
                ? null
                : () async {
                    set(() => busy = true);
                    final ok = await safely(c, () async {
                      await brain.addContext(
                        record,
                        text.text,
                        folder: folder.text.trim().isEmpty ? null : folder.text,
                      );
                      return true;
                    });
                    if (!c.mounted) return;
                    if (ok == true) {
                      Navigator.pop(c);
                    } else {
                      set(() => busy = false);
                    }
                  },
            child: busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Сохранить'),
          ),
        ],
      ),
    ),
  );
  text.dispose();
  folder.dispose();
}

Future<void> openOriginal(
  BuildContext context,
  BrainController brain,
  ArchiveRecord record,
) async {
  final file = await safely(context, () => brain.original(record));
  if (file == null || !context.mounted) return;
  final ext = (record.data['filename'] as String? ?? '').toLowerCase();
  if (ext.endsWith('.pdf')) {
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => PdfScreen(file: file, title: record.title),
      ),
    );
    return;
  }
  if (record.kind == 'photo') {
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => Scaffold(
          appBar: AppBar(title: Text(record.title)),
          body: Center(
            child: InteractiveViewer(
              minScale: .5,
              maxScale: 6,
              child: Image.file(
                file,
                errorBuilder: (_, _, _) => const Text(
                  'Не удалось показать фото. Используй «Поделиться».',
                ),
              ),
            ),
          ),
        ),
      ),
    );
    return;
  }
  if (Platform.isMacOS) {
    await Process.run('open', [file.path]);
    return;
  }
  final result = await OpenFilex.open(file.path);
  if (result.type != ResultType.done && context.mounted) {
    notice(
      context,
      'На устройстве нет приложения для этого формата. Оригинал можно передать через «Поделиться».',
    );
  }
}

Future<void> shareOriginal(
  BuildContext context,
  BrainController brain,
  ArchiveRecord record,
) async {
  if (record.blob != null) {
    final file = await safely(context, () => brain.original(record));
    if (file != null) {
      await SharePlus.instance.share(
        ShareParams(files: [XFile(file.path)], title: record.title),
      );
    }
  } else {
    await SharePlus.instance.share(
      ShareParams(text: record.data['body'] as String? ?? record.title),
    );
  }
}

Future<void> showRecordActions(
  BuildContext context,
  BrainController brain,
  ArchiveRecord record,
) async {
  await showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (c) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Icon(Icons.edit_note),
            title: const Text('Добавить пояснение'),
            onTap: () {
              Navigator.pop(c);
              editContext(context, brain, record);
            },
          ),
          ListTile(
            leading: const Icon(Icons.ios_share),
            title: const Text('Поделиться'),
            onTap: () {
              Navigator.pop(c);
              safely(context, () => shareOriginal(context, brain, record));
            },
          ),
          ListTile(
            leading: const Icon(Icons.delete_outline),
            title: const Text('В корзину'),
            onTap: () async {
              Navigator.pop(c);
              await safely(context, () => brain.trashRecord(record));
            },
          ),
        ],
      ),
    ),
  );
}

class DetailPage extends StatelessWidget {
  final BrainController brain;
  final String id;
  const DetailPage({super.key, required this.brain, required this.id});
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: brain,
    builder: (context, _) {
      final record = brain.archive.get(id);
      if (record == null) {
        return Scaffold(
          appBar: AppBar(),
          body: const EmptyState(
            icon: Icons.search_off,
            title: 'Запись не найдена',
            message: 'Возможно, архив ещё синхронизируется.',
          ),
        );
      }
      final data = record.data,
          pending = brain.pending > 0 && data['analysis_state'] != 'done';
      return Scaffold(
        appBar: AppBar(
          title: Text(
            record.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          actions: [
            IconButton(
              tooltip: 'Действия',
              icon: const Icon(Icons.more_vert),
              onPressed: () => showRecordActions(context, brain, record),
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(20, 10, 20, 32),
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: surface,
                    borderRadius: BorderRadius.circular(18),
                  ),
                  child: Icon(fileIcon(record.kind), color: accent, size: 30),
                ),
                const SizedBox(width: 15),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        record.title,
                        style: const TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        '${record.folder} · ${dayLabel(data['created'] as int? ?? record.current.created)}',
                        style: const TextStyle(color: muted, fontSize: 12),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 22),
            if (record.conflict) ...[
              SurfaceBox(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Есть изменения с двух устройств',
                      style: TextStyle(color: accent),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Обе версии сохранены. Выбери подходящую перед дальнейшим редактированием.',
                      style: TextStyle(color: muted, fontSize: 13),
                    ),
                    const SizedBox(height: 12),
                    OutlinedButton(
                      onPressed: () => _versions(context, record),
                      child: const Text('Сравнить версии'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (record.deleted) ...[
              SurfaceBox(
                child: Column(
                  children: [
                    const Text('Запись в корзине'),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: () => safely(
                        context,
                        () => brain.trashRecord(record, restore: true),
                      ),
                      child: const Text('Восстановить'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (record.blob != null)
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  FilledButton.icon(
                    onPressed: () => safely(
                      context,
                      () => openOriginal(context, brain, record),
                    ),
                    icon: const Icon(Icons.open_in_new),
                    label: const Text('Открыть оригинал'),
                  ),
                  OutlinedButton.icon(
                    onPressed: () => safely(
                      context,
                      () => shareOriginal(context, brain, record),
                    ),
                    icon: const Icon(Icons.ios_share),
                    label: const Text('Поделиться'),
                  ),
                ],
              ),
            if (record.blob == null &&
                (data['body'] as String? ?? '').isNotEmpty)
              SurfaceBox(
                child: SelectableText(
                  data['body'] as String,
                  style: const TextStyle(fontSize: 16, height: 1.55),
                ),
              ),
            if (record.kind == 'link') ...[
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => safely(context, () async {
                  final url = Uri.tryParse((data['body'] as String).trim());
                  if (url != null && ['https', 'http'].contains(url.scheme)) {
                    await launchUrl(url, mode: LaunchMode.externalApplication);
                  }
                }),
                icon: const Icon(Icons.open_in_browser),
                label: const Text('Открыть ссылку'),
              ),
            ],
            const SizedBox(height: 22),
            SurfaceBox(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Твоё пояснение',
                    style: TextStyle(fontWeight: FontWeight.w500),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    (data['context'] as String? ?? '').isEmpty
                        ? 'Добавь личный контекст, чтобы легче находить этот материал.'
                        : data['context'] as String,
                    style: const TextStyle(color: muted, height: 1.5),
                  ),
                  const SizedBox(height: 14),
                  OutlinedButton.icon(
                    onPressed: record.conflict
                        ? null
                        : () => editContext(context, brain, record),
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Изменить'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            SurfaceBox(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          'Как разобран материал',
                          style: TextStyle(fontWeight: FontWeight.w500),
                        ),
                      ),
                      if (pending)
                        const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Text(
                    data['summary'] as String? ??
                        (brain.ai.enabled
                            ? 'Разбор ещё не завершён.'
                            : 'Подключи Gemini в настройках для анализа содержимого.'),
                    style: const TextStyle(height: 1.5),
                  ),
                  if ((data['reading_note'] as String? ?? '').isNotEmpty) ...[
                    const SizedBox(height: 12),
                    Text(
                      data['reading_note'] as String,
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
                  ],
                  if (data['needs_context'] == true) ...[
                    const SizedBox(height: 12),
                    const Text(
                      'Есть неопределённость — поможет твоё пояснение.',
                      style: TextStyle(color: accent, fontSize: 12),
                    ),
                  ],
                  if ((data['analysis_error'] as String? ?? '').isNotEmpty) ...[
                    const SizedBox(height: 12),
                    Text(
                      data['analysis_error'] as String,
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
                  ],
                  if (data['topics'] is List) ...[
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 5,
                      runSpacing: 4,
                      children: (data['topics'] as List)
                          .whereType<String>()
                          .map(
                            (s) => Chip(
                              label: Text(
                                s,
                                style: const TextStyle(fontSize: 11),
                              ),
                              visualDensity: VisualDensity.compact,
                            ),
                          )
                          .toList(),
                    ),
                  ],
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: !brain.ai.enabled || record.conflict
                        ? null
                        : () {
                            brain.schedule(id);
                            notice(
                              context,
                              'Материал поставлен на повторный разбор.',
                            );
                          },
                    icon: const Icon(Icons.refresh),
                    label: const Text('Разобрать заново'),
                  ),
                  for (final source
                      in (data['sources'] as List? ?? []).whereType<Map>())
                    TextButton(
                      onPressed: () {
                        final u = Uri.tryParse(source['url'] as String? ?? '');
                        if (u != null &&
                            u.scheme == 'https' &&
                            u.host.endsWith('wikipedia.org')) {
                          launchUrl(u, mode: LaunchMode.externalApplication);
                        }
                      },
                      child: Text(
                        source['title'] as String? ?? 'Справочный источник',
                      ),
                    ),
                ],
              ),
            ),
            if ((data['text'] as String? ?? '').trim().isNotEmpty) ...[
              const SizedBox(height: 16),
              SurfaceBox(
                child: ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(bottom: 16),
                  title: const Text('Прочитанный текст'),
                  children: [
                    SelectableText(
                      data['text'] as String,
                      style: const TextStyle(fontSize: 13, height: 1.5),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      );
    },
  );
  Future<void> _versions(BuildContext context, ArchiveRecord record) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (c) => SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Сохранённые версии',
                  style: TextStyle(fontSize: 22),
                ),
                const SizedBox(height: 18),
                ...record.heads.map(
                  (r) => Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: SurfaceBox(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${dayLabel(r.created)} · ${timeLabel(r.created)}',
                            style: const TextStyle(color: muted, fontSize: 12),
                          ),
                          const SizedBox(height: 10),
                          Text(r.data['title'] as String? ?? record.title),
                          const SizedBox(height: 8),
                          Text(
                            r.data['context'] as String? ??
                                r.data['body'] as String? ??
                                '',
                            maxLines: 8,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            r.data['deleted'] == true
                                ? 'Перемещено в корзину'
                                : r.data['folder'] as String? ?? '',
                          ),
                          const SizedBox(height: 14),
                          FilledButton(
                            onPressed: () async {
                              final ok = await safely(c, () async {
                                await brain.resolve(record, r);
                                return true;
                              });
                              if (ok == true && c.mounted) Navigator.pop(c);
                            },
                            child: const Text('Использовать эту версию'),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
}

class PdfScreen extends StatefulWidget {
  final File file;
  final String title;
  const PdfScreen({super.key, required this.file, required this.title});
  @override
  State<PdfScreen> createState() => _PdfState();
}

class _PdfState extends State<PdfScreen> {
  late final PdfControllerPinch controller = PdfControllerPinch(
    document: PdfDocument.openFile(widget.file.path),
  );
  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(widget.title)),
    body: PdfViewPinch(controller: controller),
  );
}
