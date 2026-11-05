import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'dart:io';

import '../core/controller.dart';
import 'widgets.dart';

class VaultPage extends StatefulWidget {
  final BrainController brain;
  final String initialQuery;
  const VaultPage({super.key, required this.brain, this.initialQuery = ''});
  @override
  State<VaultPage> createState() => _VaultState();
}

class _VaultState extends State<VaultPage> {
  final master = TextEditingController(), repeat = TextEditingController();
  late final TextEditingController search = TextEditingController(
    text: widget.initialQuery
        .replaceAll(
          RegExp(r'данные|пароль|логин|найди|покажи', caseSensitive: false),
          '',
        )
        .trim(),
  );
  bool busy = false, visible = false;
  String? error;
  bool _previouslyOpen = false;
  void _guardDialogs() {
    final open = widget.brain.vaultOpen;
    if (_previouslyOpen && !open && mounted) {
      Navigator.of(context).popUntil((route) => route is! PopupRoute);
    }
    _previouslyOpen = open;
  }

  @override
  void initState() {
    super.initState();
    _previouslyOpen = widget.brain.vaultOpen;
    widget.brain.addListener(_guardDialogs);
    if (Platform.isAndroid) {
      const MethodChannel('app.izbrainoe/security')
          .invokeMethod<void>('protect', {'enabled': true})
          .catchError((_) {});
    }
  }

  @override
  void dispose() {
    widget.brain.removeListener(_guardDialogs);
    if (Platform.isAndroid) {
      const MethodChannel('app.izbrainoe/security')
          .invokeMethod<void>('protect', {'enabled': false})
          .catchError((_) {});
    }
    master.dispose();
    repeat.dispose();
    search.dispose();
    super.dispose();
  }

  Future<void> _unlock() async {
    if (busy) return;
    if (widget.brain.vaultRecord == null && master.text != repeat.text) {
      setState(() => error = 'Пароли не совпадают.');
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.brain.unlockVault(master.text);
      master.clear();
      repeat.clear();
    } catch (_) {
      if (mounted) {
        setState(
          () => error = widget.brain.vaultRecord == null
              ? 'Главный пароль должен содержать минимум 8 символов.'
              : 'Пароль не подошёл или хранилище повреждено.',
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.brain,
    builder: (context, _) {
      final brain = widget.brain;
      if (!brain.vaultOpen) {
        return Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(22, 22, 22, 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Пароли — под замком',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 26, fontWeight: FontWeight.w500),
                  ),
                  const SizedBox(height: 25),
                  SurfaceBox(
                    padding: const EdgeInsets.all(23),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const Center(
                          child: Icon(
                            Icons.lock_outline,
                            color: accent,
                            size: 40,
                          ),
                        ),
                        const SizedBox(height: 22),
                        Text(
                          brain.vaultRecord == null
                              ? 'Создать хранилище'
                              : 'Открыть хранилище',
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                        const SizedBox(height: 10),
                        Text(
                          brain.vaultRecord == null
                              ? 'Придумай главный пароль от 8 символов. Он понадобится на всех твоих устройствах.'
                              : 'Введи главный пароль.',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: muted, height: 1.45),
                        ),
                        const SizedBox(height: 22),
                        TextField(
                          controller: master,
                          obscureText: !visible,
                          enableSuggestions: false,
                          autocorrect: false,
                          onSubmitted: (_) {
                            if (brain.vaultRecord != null) _unlock();
                          },
                          decoration: InputDecoration(
                            labelText: 'Главный пароль',
                            suffixIcon: IconButton(
                              tooltip: visible ? 'Скрыть' : 'Показать',
                              onPressed: () =>
                                  setState(() => visible = !visible),
                              icon: Icon(
                                visible
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                              ),
                            ),
                          ),
                        ),
                        if (brain.vaultRecord == null) ...[
                          const SizedBox(height: 14),
                          TextField(
                            controller: repeat,
                            obscureText: true,
                            enableSuggestions: false,
                            autocorrect: false,
                            onSubmitted: (_) => _unlock(),
                            decoration: const InputDecoration(
                              labelText: 'Повтори главный пароль',
                            ),
                          ),
                        ],
                        if (error != null) ...[
                          const SizedBox(height: 12),
                          Text(
                            error!,
                            style: TextStyle(
                              color: Theme.of(context).colorScheme.error,
                              fontSize: 13,
                            ),
                          ),
                        ],
                        const SizedBox(height: 20),
                        FilledButton.icon(
                          onPressed: busy ? null : _unlock,
                          icon: busy
                              ? const SizedBox(
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Icon(Icons.lock_open),
                          label: Text(
                            brain.vaultRecord == null
                                ? 'Создать'
                                : 'Разблокировать',
                          ),
                        ),
                        const SizedBox(height: 15),
                        const Text(
                          'Автоблокировка через 2 минуты бездействия и при сворачивании.',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: muted,
                            fontSize: 12,
                            height: 1.5,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      }
      final q = search.text.toLowerCase().trim();
      final entries =
          brain.credentials
              .where(
                (e) => '${e['service']} ${e['login']} ${e['aliases'] ?? ''}'
                    .toLowerCase()
                    .contains(q),
              )
              .toList()
            ..sort(
              (a, b) =>
                  (a['service'] as String).compareTo(b['service'] as String),
            );
      return ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Только для тебя',
                  style: TextStyle(fontSize: 25, fontWeight: FontWeight.w500),
                ),
              ),
              IconButton(
                tooltip: 'Заблокировать',
                onPressed: brain.lockVault,
                icon: const Icon(Icons.lock_outline, color: accent),
              ),
            ],
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: () => _edit(context),
            icon: const Icon(Icons.add),
            label: const Text('Добавить пароль'),
          ),
          const SizedBox(height: 18),
          TextField(
            controller: search,
            onChanged: (_) {
              brain.touchVault();
              setState(() {});
            },
            decoration: const InputDecoration(
              hintText: 'Название сервиса или логин',
              prefixIcon: Icon(Icons.search, color: accent),
            ),
          ),
          const SizedBox(height: 20),
          if (entries.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 30),
              child: Text(
                'Здесь пока нет подходящих записей.',
                textAlign: TextAlign.center,
                style: TextStyle(color: muted),
              ),
            ),
          ...entries.map(
            (e) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: SurfaceBox(
                padding: EdgeInsets.zero,
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 8,
                  ),
                  leading: const Icon(Icons.key_outlined, color: accent),
                  title: Text(e['service'] as String),
                  subtitle: Text(
                    e['login'] as String? ?? '',
                    style: const TextStyle(color: muted),
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _showCredential(context, e),
                ),
              ),
            ),
          ),
          const SizedBox(height: 12),
          TextButton(
            onPressed: () => _changePassword(context),
            child: const Text('Изменить главный пароль'),
          ),
        ],
      );
    },
  );
  Future<void> _showCredential(
    BuildContext context,
    Map<String, dynamic> entry,
  ) async {
    widget.brain.touchVault();
    var show = false;
    await showBrainDialog<void>(
      context: context,
      builder: (c) => AnimatedBuilder(
        animation: widget.brain,
        builder: (c, _) => StatefulBuilder(
          builder: (c, set) => AlertDialog(
            title: Text(
              widget.brain.vaultOpen
                  ? entry['service'] as String
                  : 'Хранилище закрыто',
            ),
            content: widget.brain.vaultOpen
                ? SingleChildScrollView(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Логин',
                          style: TextStyle(color: muted, fontSize: 12),
                        ),
                        const SizedBox(height: 7),
                        SelectableText(entry['login'] as String? ?? ''),
                        const SizedBox(height: 8),
                        OutlinedButton.icon(
                          onPressed: () {
                            widget.brain.touchVault();
                            copySecret(c, entry['login'] as String? ?? '');
                          },
                          icon: const Icon(Icons.copy, size: 17),
                          label: const Text('Скопировать логин'),
                        ),
                        const SizedBox(height: 18),
                        const Text(
                          'Пароль',
                          style: TextStyle(color: muted, fontSize: 12),
                        ),
                        const SizedBox(height: 7),
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                show
                                    ? entry['password'] as String? ?? ''
                                    : '••••••••••••',
                                style: const TextStyle(fontSize: 17),
                              ),
                            ),
                            IconButton(
                              tooltip: show ? 'Скрыть' : 'Показать',
                              onPressed: () {
                                widget.brain.touchVault();
                                set(() => show = !show);
                              },
                              icon: Icon(
                                show
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                              ),
                            ),
                          ],
                        ),
                        OutlinedButton.icon(
                          onPressed: () {
                            widget.brain.touchVault();
                            copySecret(c, entry['password'] as String? ?? '');
                          },
                          icon: const Icon(Icons.copy, size: 17),
                          label: const Text('Скопировать пароль'),
                        ),
                        if ((entry['note'] as String? ?? '').isNotEmpty) ...[
                          const SizedBox(height: 18),
                          Text(entry['note'] as String),
                        ],
                      ],
                    ),
                  )
                : const Text('Разблокируй хранилище заново.'),
            actions: [
              if (widget.brain.vaultOpen)
                TextButton(
                  onPressed: () {
                    Navigator.pop(c);
                    _edit(context, entry: entry);
                  },
                  child: const Text('Изменить'),
                ),
              TextButton(
                onPressed: () => Navigator.pop(c),
                child: const Text('Закрыть'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _edit(
    BuildContext context, {
    Map<String, dynamic>? entry,
  }) async {
    widget.brain.touchVault();
    final service = TextEditingController(
          text: entry?['service'] as String? ?? '',
        ),
        login = TextEditingController(text: entry?['login'] as String? ?? ''),
        password = TextEditingController(
          text: entry?['password'] as String? ?? '',
        ),
        note = TextEditingController(text: entry?['note'] as String? ?? '');
    var saving = false, show = false;
    String? failure;
    await showBrainDialog<void>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          title: Text(entry == null ? 'Новый пароль' : 'Изменить пароль'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: service,
                  onChanged: (_) => widget.brain.touchVault(),
                  autofocus: true,
                  decoration: const InputDecoration(
                    labelText: 'Сервис',
                    hintText: 'Например, РХТУ',
                  ),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: login,
                  onChanged: (_) => widget.brain.touchVault(),
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(labelText: 'Логин'),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: password,
                  onChanged: (_) => widget.brain.touchVault(),
                  obscureText: !show,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: InputDecoration(
                    labelText: 'Пароль',
                    suffixIcon: IconButton(
                      tooltip: show ? 'Скрыть' : 'Показать',
                      onPressed: () => set(() => show = !show),
                      icon: Icon(
                        show
                            ? Icons.visibility_off_outlined
                            : Icons.visibility_outlined,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: note,
                  onChanged: (_) => widget.brain.touchVault(),
                  minLines: 1,
                  maxLines: 3,
                  decoration: const InputDecoration(
                    labelText: 'Пояснение — необязательно',
                  ),
                ),
                if (failure != null) ...[
                  const SizedBox(height: 12),
                  Text(
                    failure!,
                    style: TextStyle(color: Theme.of(c).colorScheme.error),
                  ),
                ],
              ],
            ),
          ),
          actions: [
            if (entry != null)
              TextButton(
                onPressed: saving
                    ? null
                    : () async {
                        if (!await confirm(
                          c,
                          'Удалить пароль?',
                          'Запись будет скрыта из хранилища.',
                        )) {
                          return;
                        }
                        if (!c.mounted) return;
                        final ok = await safely(c, () async {
                          await widget.brain.saveCredential(
                            id: entry['id'] as String,
                            service: service.text,
                            login: login.text,
                            password: password.text,
                            note: note.text,
                            deleted: true,
                          );
                          return true;
                        });
                        if (ok == true && c.mounted) Navigator.pop(c);
                      },
                child: const Text('Удалить'),
              ),
            TextButton(
              onPressed: saving ? null : () => Navigator.pop(c),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: saving
                  ? null
                  : () async {
                      if (service.text.trim().isEmpty) {
                        set(() => failure = 'Укажи название сервиса.');
                        return;
                      }
                      set(() {
                        saving = true;
                        failure = null;
                      });
                      try {
                        await widget.brain.saveCredential(
                          id: entry?['id'] as String?,
                          service: service.text,
                          login: login.text,
                          password: password.text,
                          note: note.text,
                        );
                        if (c.mounted) Navigator.pop(c);
                      } catch (e) {
                        if (c.mounted) {
                          set(() {
                            failure = BrainController.friendly(e);
                            saving = false;
                          });
                        }
                      }
                    },
              child: saving
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
    service.clear();
    login.clear();
    password.clear();
    note.clear();
    service.dispose();
    login.dispose();
    password.dispose();
    note.dispose();
  }

  Future<void> _changePassword(BuildContext context) async {
    final first = TextEditingController(), second = TextEditingController();
    var saving = false;
    String? failure;
    await showBrainDialog<void>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          title: const Text('Новый главный пароль'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: first,
                onChanged: (_) => widget.brain.touchVault(),
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'Минимум 8 символов',
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: second,
                onChanged: (_) => widget.brain.touchVault(),
                obscureText: true,
                decoration: const InputDecoration(labelText: 'Повтори пароль'),
              ),
              if (failure != null) ...[
                const SizedBox(height: 12),
                Text(failure!),
              ],
            ],
          ),
          actions: [
            TextButton(
              onPressed: saving ? null : () => Navigator.pop(c),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: saving
                  ? null
                  : () async {
                      if (first.text != second.text) {
                        set(() => failure = 'Пароли не совпадают.');
                        return;
                      }
                      set(() => saving = true);
                      try {
                        await widget.brain.changeMasterPassword(first.text);
                        if (c.mounted) Navigator.pop(c);
                      } catch (e) {
                        if (c.mounted) {
                          set(() {
                            saving = false;
                            failure = BrainController.friendly(e);
                          });
                        }
                      }
                    },
              child: const Text('Сохранить'),
            ),
          ],
        ),
      ),
    );
    first.clear();
    second.clear();
    first.dispose();
    second.dispose();
  }
}
