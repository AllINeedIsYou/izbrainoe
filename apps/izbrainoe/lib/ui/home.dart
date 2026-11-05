import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import '../core/share_inbox.dart';
import 'detail.dart';
import 'search.dart';
import 'settings.dart';
import 'vault.dart';
import 'widgets.dart';
import 'collections.dart';
import 'attachments.dart';
import 'chat_bubble.dart';

class HomeScreen extends StatefulWidget {
  final BrainController brain;
  final ShareInbox? shares;
  const HomeScreen({super.key, required this.brain, this.shares});
  @override
  State<HomeScreen> createState() => _HomeState();
}

class _HomeState extends State<HomeScreen> with WidgetsBindingObserver {
  int tab = 0;
  bool sending = false;
  late String _seenArchiveCode;
  void _archiveChanged() {
    if (_seenArchiveCode != brain.archiveCode) {
      _seenArchiveCode = brain.archiveCode;
      composer.clear();
      if (mounted) setState(() => tab = 0);
    }
  }

  final composer = TextEditingController();
  final scroll = ScrollController();
  StreamSubscription<List<SharedMediaFile>>? sharing;
  BrainController get brain => widget.brain;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _seenArchiveCode = brain.archiveCode;
    brain.addListener(_archiveChanged);
    if (widget.shares != null) {
      widget.shares!.attach(_receive);
    } else if (Platform.isAndroid) {
      sharing = ReceiveSharingIntent.instance.getMediaStream().listen(_receive);
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
    brain.removeListener(_archiveChanged);
    widget.shares?.detach();
    sharing?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    composer.dispose();
    scroll.dispose();
    super.dispose();
  }

  Future<void> _pickFiles() async {
    final saved = await safely(context, () => addAttachments(context, brain));
    if (mounted && (saved ?? 0) > 0) setState(() => tab = 0);
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
              child: const Icon(Icons.bookmark_outline, size: 32),
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
                  ? FoldersView(brain: brain)
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
            ChatBubble(
              key: ValueKey(record.id),
              brain: brain,
              record: record,
              onTap: () => _detail(record),
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
}
