import 'package:flutter/material.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import 'attachments.dart';
import 'detail.dart';
import 'material_preview.dart';
import 'widgets.dart';

Future<void> newFolderDialog(
  BuildContext context,
  BrainController brain, {
  String? existingFolder,
}) async {
  final old = brain.folderDefinitions
      .where((r) => r.folder == existingFolder)
      .firstOrNull;
  final name = TextEditingController(text: existingFolder ?? ''),
      description = TextEditingController(
        text: old?.data['description'] as String? ?? '',
      );
  var review = false, busy = false;
  await showBrainDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (c) => StatefulBuilder(
      builder: (c, set) => PopScope(
        canPop: !busy,
        child: AlertDialog(
          title: Text(existingFolder == null ? 'Новая папка' : 'Правило папки'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TextField(
                  controller: name,
                  enabled: !busy && existingFolder == null,
                  autofocus: existingFolder == null,
                  decoration: const InputDecoration(labelText: 'Название'),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: description,
                  enabled: !busy,
                  minLines: 2,
                  maxLines: 5,
                  maxLength: 3000,
                  decoration: const InputDecoration(
                    labelText: 'Что сохранять в эту папку',
                    hintText: 'Например: всё, что связано с ресторанами. Можно указать и форматы: mp3, flac.',
                  ),
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: review,
                  onChanged: busy
                      ? null
                      : (v) => set(() => review = v ?? false),
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text(
                    'Проанализировать другие папки на нужные файлы',
                    style: TextStyle(fontSize: 13),
                  ),
                ),
                const Text(
                  'Новое подходящее правило имеет приоритет. Без галочки старые файлы остаются на месте.',
                  style: TextStyle(fontSize: 12, color: muted),
                ),
                if (busy) ...[
                  const SizedBox(height: 15),
                  const LinearProgressIndicator(),
                  const SizedBox(height: 8),
                  AnimatedBuilder(
                    animation: brain,
                    builder: (_, _) => Text(
                      brain.reviewingRule
                          ? brain.ruleReviewStatus
                          : 'Сохраняю правило…',
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
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
                        await brain.createFolderRule(
                          name.text,
                          description.text,
                          reviewExisting: review,
                        );
                        return true;
                      });
                      if (!c.mounted) return;
                      if (ok == true) {
                        Navigator.pop(c);
                        if (context.mounted) {
                          notice(
                            context,
                            review && brain.ruleReviewStatus.isNotEmpty
                                ? brain.ruleReviewStatus
                                : 'Правило сохранено',
                          );
                        }
                      } else {
                        set(() => busy = false);
                      }
                    },
              child: Text(existingFolder == null ? 'Создать' : 'Сохранить'),
            ),
          ],
        ),
      ),
    ),
  );
  name.dispose();
  description.dispose();
}

class FoldersView extends StatefulWidget {
  final BrainController brain;
  const FoldersView({super.key, required this.brain});
  @override
  State<FoldersView> createState() => _FoldersViewState();
}

class _FoldersViewState extends State<FoldersView> {
  String query = '';
  bool alphabetical = false;
  BrainController get brain => widget.brain;
  void open(String folder) => Navigator.push(
    context,
    MaterialPageRoute<void>(
      builder: (_) => FolderPage(brain: brain, folder: folder),
    ),
  );
  @override
  Widget build(BuildContext context) {
    final all = brain.folders
        .where((f) => f.toLowerCase().contains(query.toLowerCase()))
        .toList();
    final pins = query.isEmpty ? brain.pinnedFolders : <String>[];
    final rest = all.where((f) => !pins.contains(f)).toList();
    int count(String f) => brain.materials.where((r) => r.folder == f).length;
    if (alphabetical) {
      rest.sort((a, b) => a.compareTo(b));
    } else {
      rest.sort((a, b) {
        int latest(String f) => brain.materials
            .where((r) => r.folder == f)
            .map((r) => r.current.created)
            .fold(0, (a, b) => a > b ? a : b);
        return latest(b).compareTo(latest(a));
      });
    }
    Widget grid(List<String> names, {bool pinned = false}) => GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        crossAxisSpacing: 10,
        mainAxisSpacing: 10,
        mainAxisExtent: 125,
      ),
      itemCount: names.length,
      itemBuilder: (context, i) {
        final f = names[i],
            bg = pinned
                ? (i.isEven ? accent : const Color(0xffc6c6c6))
                : (i.isEven
                      ? const Color(0xff292929)
                      : const Color(0xff343434)),
            fg = pinned ? background : accent;
        return Material(
          color: bg,
          borderRadius: BorderRadius.circular(5),
          child: InkWell(
            onTap: () => open(f),
            onLongPress: () => folderSettings(context, brain, f),
            borderRadius: BorderRadius.circular(5),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      FolderSymbol(f, color: fg),
                      if (pinned)
                        Icon(Icons.push_pin_outlined, size: 13, color: fg),
                    ],
                  ),
                  const Spacer(),
                  Text(
                    f,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontWeight: FontWeight.w500,
                      fontSize: 15,
                      height: 1.1,
                      color: fg,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    '${count(f)} материалов',
                    style: TextStyle(fontSize: 11, color: fg),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 10, 20, 25),
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text(
              'КОЛЛЕКЦИИ',
              style: TextStyle(
                fontSize: 28,
                fontWeight: FontWeight.w600,
                letterSpacing: -1,
              ),
            ),
            IconButton(
              tooltip: 'Создать подборку',
              onPressed: () => newFolderDialog(context, brain),
              icon: const Icon(Icons.add),
            ),
          ],
        ),
        const SizedBox(height: 12),
        TextField(
          onChanged: (v) => setState(() => query = v),
          decoration: const InputDecoration(
            hintText: 'Найти папку…',
            prefixIcon: Icon(Icons.search),
            contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 12),
          ),
        ),
        if (pins.isNotEmpty) ...[
          const SizedBox(height: 23),
          const Text(
            'ПОД РУКОЙ',
            style: TextStyle(fontSize: 11, color: muted, letterSpacing: 1),
          ),
          const SizedBox(height: 12),
          grid(pins, pinned: true),
        ],
        const SizedBox(height: 16),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '${query.isNotEmpty
                  ? 'НАЙДЕНО'
                  : pins.isEmpty
                  ? 'ВСЕ ПАПКИ'
                  : 'ОСТАЛЬНЫЕ'} · ${rest.length}',
              style: const TextStyle(
                fontSize: 11,
                color: muted,
                letterSpacing: 1,
              ),
            ),
            DropdownButton<bool>(
              value: alphabetical,
              underline: const SizedBox(),
              style: const TextStyle(color: muted, fontSize: 12),
              items: const [
                DropdownMenuItem(value: false, child: Text('По обновлению')),
                DropdownMenuItem(value: true, child: Text('По алфавиту')),
              ],
              onChanged: (v) => setState(() => alphabetical = v ?? false),
            ),
          ],
        ),
        if (rest.isNotEmpty) grid(rest),
        if (all.isEmpty)
          const Padding(
            padding: EdgeInsets.all(30),
            child: Text(
              'Папок пока нет',
              textAlign: TextAlign.center,
              style: TextStyle(color: muted),
            ),
          ),
      ],
    );
  }
}

Future<bool> folderSettings(
  BuildContext context,
  BrainController brain,
  String folder,
) async {
  final action = await showModalBottomSheet<String>(
    context: context,
    showDragHandle: true,
    builder: (c) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            title: Text(
              folder,
              style: const TextStyle(fontWeight: FontWeight.w500),
            ),
            leading: FolderSymbol(folder),
          ),
          ListTile(
            leading: const Icon(Icons.push_pin_outlined),
            title: Text(
              brain.pinnedFolders.contains(folder)
                  ? 'Открепить папку'
                  : 'Закрепить папку',
            ),
            onTap: () => Navigator.pop(c, 'pin'),
          ),
          ListTile(
            leading: const Icon(Icons.auto_awesome_outlined),
            title: const Text('Правило папки'),
            onTap: () => Navigator.pop(c, 'rule'),
          ),
          ListTile(
            leading: const Icon(Icons.checklist),
            title: const Text('Выбрать несколько файлов'),
            onTap: () => Navigator.pop(c, 'select'),
          ),
          ListTile(
            leading: const Icon(Icons.delete_outline),
            title: const Text('Удалить папку и содержимое'),
            onTap: () => Navigator.pop(c, 'delete'),
          ),
          const SizedBox(height: 12),
        ],
      ),
    ),
  );
  if (!context.mounted) return false;
  if (action == 'pin') await safely(context, () => brain.pinFolder(folder));
  if (action == 'rule' && context.mounted) {
    await newFolderDialog(context, brain, existingFolder: folder);
  }
  if (action == 'delete' &&
      context.mounted &&
      await confirmDelete(
        context,
        'Удалить папку «$folder»?',
        'Все её материалы будут перемещены в корзину, правило папки будет удалено.',
      )) {
    if (!context.mounted) return false;
    final ok = await safely(context, () async {
      await brain.deleteFolder(folder);
      return true;
    });
    return ok == true;
  }
  return action == 'select';
}

class FolderPage extends StatefulWidget {
  final BrainController brain;
  final String folder;
  const FolderPage({super.key, required this.brain, required this.folder});
  @override
  State<FolderPage> createState() => _FolderPageState();
}

class _FolderPageState extends State<FolderPage> {
  final selected = <String>{};
  bool selecting = false;
  BrainController get brain => widget.brain;
  void select(ArchiveRecord r) => setState(() {
    selecting = true;
    if (!selected.add(r.id)) selected.remove(r.id);
  });
  Future<void> remove() async {
    if (selected.isEmpty) return;
    if (!await confirmDelete(
      context,
      'Удалить выбранное?',
      'В корзину попадёт материалов: ${selected.length}.',
    )) {
      return;
    }
    if (!mounted) return;
    await safely(context, () => brain.trashMany(selected.toList()));
    if (mounted) {
      setState(() {
        selected.clear();
        selecting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: brain,
    builder: (context, _) {
      final items = brain.materials
          .where((r) => r.folder == widget.folder)
          .toList();
      selected.removeWhere((id) => !items.any((r) => r.id == id));
      final grid = brain.folderGrid(widget.folder);
      void open(ArchiveRecord r) {
        if (selecting) {
          select(r);
        } else {
          Navigator.push(
            context,
            MaterialPageRoute<void>(
              builder: (_) => DetailPage(brain: brain, id: r.id),
            ),
          );
        }
      }

      return Scaffold(
        appBar: AppBar(
          title: Text(
            selecting ? 'Выбрано: ${selected.length}' : widget.folder,
          ),
          leading: selecting
              ? IconButton(
                  tooltip: 'Завершить выбор',
                  onPressed: () => setState(() {
                    selecting = false;
                    selected.clear();
                  }),
                  icon: const Icon(Icons.close),
                )
              : null,
          actions: [
            if (selecting) ...[
              IconButton(
                tooltip: 'Выбрать все',
                onPressed: () =>
                    setState(() => selected.addAll(items.map((r) => r.id))),
                icon: const Icon(Icons.select_all),
              ),
              IconButton(
                tooltip: 'Удалить выбранное',
                onPressed: selected.isEmpty ? null : remove,
                icon: const Icon(Icons.delete_outline),
              ),
            ] else ...[
              IconButton(
                tooltip: grid ? 'Показать списком' : 'Показать плитками',
                onPressed: () => safely(
                  context,
                  () => brain.setFolderGrid(widget.folder, !grid),
                ),
                icon: Icon(grid ? Icons.view_list_outlined : Icons.grid_view),
              ),
              IconButton(
                tooltip: 'Настройки папки',
                onPressed: () async {
                  final result = await folderSettings(
                    context,
                    brain,
                    widget.folder,
                  );
                  if (!context.mounted) return;
                  if (!brain.folders.contains(widget.folder)) {
                    Navigator.pop(context);
                  } else if (result) {
                    setState(() => selecting = true);
                  }
                },
                icon: const Icon(Icons.more_vert),
              ),
            ],
          ],
        ),
        body: items.isEmpty
            ? const EmptyState(
                icon: Icons.folder_open_outlined,
                title: 'Папка пуста',
                message:
                    'Новые материалы будут появляться здесь по твоему правилу.',
              )
            : grid
            ? GridView.builder(
                padding: const EdgeInsets.all(14),
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 2,
                  crossAxisSpacing: 10,
                  mainAxisSpacing: 10,
                  mainAxisExtent:
                      (MediaQuery.sizeOf(context).width - 38) / 2 + 76,
                ),
                itemCount: items.length,
                itemBuilder: (context, i) {
                  final r = items[i];
                  return Material(
                    color: surface,
                    borderRadius: BorderRadius.circular(9),
                    clipBehavior: Clip.antiAlias,
                    child: InkWell(
                      onTap: () => open(r),
                      onLongPress: () => select(r),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          AspectRatio(
                            aspectRatio: 1,
                            child: Stack(
                              fit: StackFit.expand,
                              children: [
                                RecordPreview(brain: brain, record: r),
                                if (selecting)
                                  Positioned(
                                    top: 6,
                                    right: 6,
                                    child: Icon(
                                      selected.contains(r.id)
                                          ? Icons.check_circle
                                          : Icons.radio_button_unchecked,
                                      color: accent,
                                    ),
                                  ),
                              ],
                            ),
                          ),
                          Expanded(
                            child: Padding(
                              padding: const EdgeInsets.all(9),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    r.title,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      fontSize: 12,
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                  const Spacer(),
                                  Text(
                                    r.kind == 'photo'
                                        ? 'Фото'
                                        : r.blob != null
                                        ? sizeLabel(r.blob!['size'] as int)
                                        : 'Заметка',
                                    style: const TextStyle(
                                      color: muted,
                                      fontSize: 10,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              )
            : ListView.builder(
                padding: const EdgeInsets.all(14),
                itemCount: items.length,
                itemBuilder: (context, i) {
                  final r = items[i];
                  return GestureDetector(
                    onLongPress: () => select(r),
                    child: MaterialTile(
                      brain: brain,
                      record: r,
                      onTap: () => open(r),
                      trailing: selecting
                          ? Icon(
                              selected.contains(r.id)
                                  ? Icons.check_circle
                                  : Icons.radio_button_unchecked,
                            )
                          : null,
                    ),
                  );
                },
              ),
        floatingActionButton: selecting
            ? null
            : FloatingActionButton.small(
                tooltip: 'Добавить в папку',
                onPressed: () => safely(
                  context,
                  () => addAttachments(context, brain, folder: widget.folder),
                ),
                child: const Icon(Icons.add),
              ),
      );
    },
  );
}
