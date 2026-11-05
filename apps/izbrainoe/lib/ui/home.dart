import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import 'detail.dart';
import 'search.dart';
import 'settings.dart';
import 'vault.dart';
import 'widgets.dart';

class HomeScreen extends StatefulWidget {
  final BrainController brain;
  const HomeScreen({super.key, required this.brain});
  @override
  State<HomeScreen> createState() => _HomeState();
}

class _HomeState extends State<HomeScreen> with WidgetsBindingObserver {
  int tab = 0;
  bool sending = false;
  final composer = TextEditingController();
  final scroll = ScrollController();
  StreamSubscription<List<SharedMediaFile>>? sharing;
  BrainController get brain => widget.brain;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (Platform.isAndroid) {
      sharing = ReceiveSharingIntent.instance.getMediaStream().listen(
        _receive,
        onError: (_) {
          if (mounted) {
            notice(
              context,
              'Не удалось получить вложение. Попробуй выбрать файл в приложении.',
            );
          }
        },
      );
      unawaited(ReceiveSharingIntent.instance.getInitialMedia().then(_receive));
    }
  }

  Future<void> _receive(List<SharedMediaFile> items) async {
    if (items.isEmpty) return;
    final caption = items
        .where(
          (f) =>
              f.type == SharedMediaType.text || f.type == SharedMediaType.url,
        )
        .map((f) => f.path)
        .join('\n');
    final files = items
        .where(
          (f) =>
              f.type != SharedMediaType.text && f.type != SharedMediaType.url,
        )
        .toList();
    var failures = 0;
    for (final f in files) {
      try {
        if (f.path.toLowerCase().endsWith('.izbrainoe')) {
          await brain.restoreBackup(File(f.path));
        } else {
          await brain.addFile(File(f.path), context: caption);
        }
      } catch (_) {
        failures++;
      }
    }
    if (files.isEmpty && caption.isNotEmpty) {
      try {
        await brain.addNote(caption);
      } catch (_) {
        failures++;
      }
    }
    await ReceiveSharingIntent.instance.reset();
    if (mounted) {
      setState(() => tab = 0);
      if (failures > 0) {
        notice(
          context,
          'Не удалось сохранить вложений: $failures. Исходные файлы не удалены.',
        );
      }
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      brain.resume();
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive ||
        state == AppLifecycleState.detached) {
      brain.pause();
    }
  }

  @override
  void dispose() {
    sharing?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    composer.dispose();
    scroll.dispose();
    super.dispose();
  }

  Future<void> _pickFiles() async {
    final picked = await safely(context, () => FilePicker.pickFiles());
    if (picked == null) return;
    var saved = 0;
    for (final f in picked) {
      try {
        final source = await copyPickedFile(f, brain.temporary);
        try {
          await brain.addFile(source, name: f.name);
        } finally {
          if (source.parent.path == brain.temporary.path &&
              source.path.split('/').last.startsWith('picked-') &&
              await source.exists()) {
            await source.delete();
          }
        }
        saved++;
      } catch (e) {
        if (mounted) notice(context, BrainController.friendly(e));
      }
    }
    if (mounted && saved > 0) {
      setState(() => tab = 0);
      notice(context, 'Сохранено: $saved');
    }
  }

  Future<void> _send() async {
    final text = composer.text.trim();
    if (text.isEmpty || sending) return;
    if (brain.credentialQuery(text)) {
      composer.clear();
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
          builder: (_) => Scaffold(
            appBar: AppBar(title: const Text('Пароли')),
            body: VaultPage(brain: brain, initialQuery: text),
          ),
        ),
      );
      return;
    }
    final search = RegExp(
      r'^/?(?:найди|найти|покажи|где)\s+(.+)$',
      caseSensitive: false,
      dotAll: true,
    ).firstMatch(text);
    if (search != null) {
      composer.clear();
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
          builder: (_) => SearchPage(brain: brain, initialQuery: search[1]!),
        ),
      );
      return;
    }
    setState(() => sending = true);
    final saved = await safely(context, () => brain.addNote(text));
    if (mounted) {
      setState(() => sending = false);
      if (saved != null) {
        composer.clear();
        if (scroll.hasClients) {
          unawaited(
            scroll.animateTo(
              0,
              duration: const Duration(milliseconds: 180),
              curve: Curves.easeOut,
            ),
          );
        }
      }
    }
  }

  void _detail(ArchiveRecord record) => Navigator.push(
    context,
    MaterialPageRoute<void>(
      builder: (_) => DetailPage(brain: brain, id: record.id),
    ),
  );
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: brain,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(11),
              child: Image.asset('assets/logo.png', width: 34, height: 34),
            ),
            const SizedBox(width: 11),
            const Flexible(
              child: Text(
                'Изbrainое',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.w500, fontSize: 21),
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Найти',
            icon: const Icon(Icons.search),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => SearchPage(brain: brain)),
            ),
          ),
          IconButton(
            tooltip: 'Настройки',
            icon: const Icon(Icons.more_vert),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => SettingsPage(brain: brain),
              ),
            ),
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            if (brain.importing > 0 || brain.pending > 0)
              const LinearProgressIndicator(
                minHeight: 2,
                color: accent,
                backgroundColor: surface,
              ),
            Expanded(
              child: tab == 0
                  ? _chat()
                  : tab == 2
                  ? _folders()
                  : VaultPage(brain: brain),
            ),
            if (tab == 0) _composer(),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (value) {
          if (value == 1) {
            Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => SearchPage(brain: brain)),
            );
          } else {
            setState(() => tab = value);
          }
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.chat_bubble_outline),
            selectedIcon: Icon(Icons.chat_bubble),
            label: 'Чат',
          ),
          NavigationDestination(icon: Icon(Icons.search), label: 'Поиск'),
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            selectedIcon: Icon(Icons.folder),
            label: 'Подборки',
          ),
          NavigationDestination(
            icon: Icon(Icons.lock_outline),
            selectedIcon: Icon(Icons.lock),
            label: 'Пароли',
          ),
        ],
      ),
    ),
  );
  Widget _chat() {
    final items = brain.materials;
    if (items.isEmpty) {
      return EmptyState(
        icon: Icons.bookmark_outline,
        title: 'Твой чат с собой',
        message: 'Сохрани файл, ссылку или мысль.\nИз других приложений — через «Поделиться».',
        action: FilledButton.icon(
          onPressed: _pickFiles,
          icon: const Icon(Icons.add),
          label: const Text('Добавить файл'),
        ),
      );
    }
    return ListView.builder(
      controller: scroll,
      reverse: true,
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 18),
      itemCount: items.length,
      itemBuilder: (context, i) {
        final record = items[i],
            date = dayLabel(
              record.data['created'] as int? ?? record.current.created,
            );
        final previous = i == items.length - 1
            ? null
            : dayLabel(
                items[i + 1].data['created'] as int? ??
                    items[i + 1].current.created,
              );
        return Column(
          children: [
            if (previous != date)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 15),
                child: Text(
                  date,
                  style: const TextStyle(color: muted, fontSize: 12),
                ),
              ),
            Align(
              alignment: Alignment.centerRight,
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  maxWidth: MediaQuery.sizeOf(context).width > 600
                      ? 480
                      : MediaQuery.sizeOf(context).width * .88,
                ),
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Material(
                    color: surface,
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(20),
                      topRight: Radius.circular(20),
                      bottomLeft: Radius.circular(20),
                      bottomRight: Radius.circular(5),
                    ),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(20),
                      onTap: () => _detail(record),
                      onLongPress: () =>
                          showRecordActions(context, brain, record),
                      child: Padding(
                        padding: const EdgeInsets.all(14),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (record.kind == 'photo')
                              FutureBuilder<Uint8List?>(
                                future: brain.preview(record),
                                builder: (context, snapshot) {
                                  if (snapshot.data == null) {
                                    return const SizedBox.shrink();
                                  }
                                  return Padding(
                                    padding: const EdgeInsets.only(bottom: 12),
                                    child: ClipRRect(
                                      borderRadius: BorderRadius.circular(12),
                                      child: ConstrainedBox(
                                        constraints: const BoxConstraints(
                                          maxHeight: 230,
                                        ),
                                        child: Image.memory(
                                          snapshot.data!,
                                          fit: BoxFit.contain,
                                        ),
                                      ),
                                    ),
                                  );
                                },
                              ),
                            if (record.blob != null)
                              Row(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Container(
                                    padding: const EdgeInsets.all(10),
                                    decoration: BoxDecoration(
                                      color: const Color(0xff263626),
                                      borderRadius: BorderRadius.circular(12),
                                    ),
                                    child: Icon(
                                      fileIcon(record.kind),
                                      color: accent,
                                    ),
                                  ),
                                  const SizedBox(width: 12),
                                  Flexible(
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          record.title,
                                          style: const TextStyle(
                                            fontWeight: FontWeight.w500,
                                          ),
                                        ),
                                        const SizedBox(height: 5),
                                        Text(
                                          sizeLabel(
                                            record.blob!['size'] as int,
                                          ),
                                          style: const TextStyle(
                                            color: muted,
                                            fontSize: 12,
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ],
                              )
                            else
                              SelectableText(
                                record.data['body'] as String? ?? record.title,
                                maxLines: 8,
                                style: TextStyle(
                                  fontSize: 15,
                                  height: 1.45,
                                  color: record.kind == 'link' ? accent : null,
                                ),
                              ),
                            if ((record.data['context'] as String? ?? '')
                                .isNotEmpty) ...[
                              const SizedBox(height: 10),
                              Text(
                                record.data['context'] as String,
                                style: const TextStyle(
                                  fontSize: 13,
                                  color: muted,
                                ),
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ],
                            const SizedBox(height: 9),
                            Wrap(
                              alignment: WrapAlignment.end,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              spacing: 7,
                              children: [
                                if (record.conflict)
                                  const Icon(
                                    Icons.call_split,
                                    color: accent,
                                    size: 14,
                                  ),
                                Text(
                                  record.folder,
                                  style: const TextStyle(
                                    color: muted,
                                    fontSize: 11,
                                  ),
                                ),
                                Text(
                                  timeLabel(
                                    record.data['created'] as int? ??
                                        record.current.created,
                                  ),
                                  style: const TextStyle(
                                    color: muted,
                                    fontSize: 11,
                                  ),
                                ),
                                Icon(
                                  brain.materials.any(
                                            (r) => r.id == record.id,
                                          ) &&
                                          record.data['analysis_state'] ==
                                              'pending' &&
                                          brain.ai.enabled
                                      ? Icons.schedule
                                      : Icons.check,
                                  size: 13,
                                  color: accent,
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _composer() => Padding(
    padding: const EdgeInsets.fromLTRB(12, 5, 12, 10),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        IconButton.filledTonal(
          tooltip: 'Прикрепить файл',
          onPressed: _pickFiles,
          icon: const Icon(Icons.add),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextField(
            controller: composer,
            minLines: 1,
            maxLines: 5,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              hintText: 'Сохранить мысль…',
              contentPadding: EdgeInsets.symmetric(
                horizontal: 17,
                vertical: 13,
              ),
            ),
          ),
        ),
        const SizedBox(width: 8),
        IconButton.filled(
          tooltip: 'Сохранить',
          onPressed: sending ? null : _send,
          icon: sending
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.arrow_upward),
        ),
      ],
    ),
  );
  Widget _folders() => ListView(
    padding: const EdgeInsets.all(20),
    children: [
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          const Text(
            'Подборки',
            style: TextStyle(fontSize: 26, fontWeight: FontWeight.w500),
          ),
          IconButton(
            tooltip: 'Создать подборку',
            onPressed: () => newFolderDialog(context, brain),
            icon: const Icon(Icons.create_new_folder_outlined, color: accent),
          ),
        ],
      ),
      const SizedBox(height: 20),
      if (brain.folders.isEmpty)
        const Padding(
          padding: EdgeInsets.only(top: 50),
          child: Text(
            'Подборки появятся после первых сохранений.',
            textAlign: TextAlign.center,
            style: TextStyle(color: muted),
          ),
        ),
      ...brain.folders.map(
        (folder) => Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: SurfaceBox(
            padding: EdgeInsets.zero,
            child: ListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 18,
                vertical: 11,
              ),
              leading: const Icon(Icons.folder_outlined, color: accent),
              title: Text(folder),
              subtitle: Text(
                'Материалов: ${brain.materials.where((r) => r.folder == folder).length}',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
              trailing: const Icon(Icons.chevron_right, color: muted),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => FolderPage(brain: brain, folder: folder),
                ),
              ),
            ),
          ),
        ),
      ),
    ],
  );
}

Future<void> newFolderDialog(
  BuildContext context,
  BrainController brain,
) async {
  final name = TextEditingController(), extensions = TextEditingController();
  await showBrainDialog<void>(
    context: context,
    builder: (c) => StatefulBuilder(
      builder: (c, set) {
        return AlertDialog(
          title: const Text('Новая подборка'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: name,
                  autofocus: true,
                  decoration: const InputDecoration(labelText: 'Название'),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: extensions,
                  decoration: const InputDecoration(
                    labelText: 'Расширения — необязательно',
                    hintText: 'Например, mp3, flac',
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  'Без личного пояснения файлы этих форматов будут попадать сюда. Правило применяется и к сохранённым материалам.',
                  style: TextStyle(fontSize: 12, color: muted),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: () async {
                final result = await safely(c, () async {
                  await brain.createFolderRule(name.text, extensions.text);
                  return true;
                });
                if (result == true && c.mounted) Navigator.pop(c);
              },
              child: const Text('Создать'),
            ),
          ],
        );
      },
    ),
  );
  name.dispose();
  extensions.dispose();
}

class FolderPage extends StatelessWidget {
  final BrainController brain;
  final String folder;
  const FolderPage({super.key, required this.brain, required this.folder});
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: brain,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: Text(folder)),
      body: ListView(
        padding: const EdgeInsets.all(18),
        children: brain.materials
            .where((r) => r.folder == folder)
            .map(
              (r) => MaterialTile(
                record: r,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => DetailPage(brain: brain, id: r.id),
                  ),
                ),
              ),
            )
            .toList(),
      ),
    ),
  );
}
