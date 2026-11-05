import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/controller.dart';
import 'detail.dart';
import 'widgets.dart';
import 'setup_scanner.dart';

class SettingsPage extends StatefulWidget {
  final BrainController brain;
  const SettingsPage({super.key, required this.brain});
  @override
  State<SettingsPage> createState() => _SettingsState();
}

class _SettingsState extends State<SettingsPage> {
  late final TextEditingController key = TextEditingController(
        text: widget.brain.ai.apiKey,
      ),
      name = TextEditingController(text: widget.brain.deviceName),
      model = TextEditingController(text: widget.brain.ai.model),
      limit = TextEditingController(
        text: widget.brain.ai.monthlyLimit.toStringAsFixed(0),
      );
  final token = TextEditingController();
  bool loaded = false, saving = false, exporting = false;
  @override
  void initState() {
    super.initState();
    widget.brain.secrets.read('disk_token').then((value) {
      if (mounted) {
        token.text = value ?? '';
        setState(() => loaded = true);
      }
    });
  }

  @override
  void dispose() {
    key.clear();
    token.clear();
    key.dispose();
    name.dispose();
    model.dispose();
    limit.dispose();
    token.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final amount = double.tryParse(limit.text.replaceAll(',', '.'));
    if (amount == null || amount < 0) {
      notice(context, 'Укажи лимит в рублях.');
      return;
    }
    setState(() => saving = true);
    final ok = await safely(context, () async {
      await widget.brain.saveSettings(
        key: key.text,
        token: token.text,
        name: name.text,
        model: model.text,
        limit: amount,
      );
      return true;
    });
    if (mounted) {
      setState(() => saving = false);
      if (ok == true) notice(context, 'Настройки сохранены.');
    }
  }

  Future<void> _backup() async {
    setState(() => exporting = true);
    await safely(context, () async {
      final file = await widget.brain.backup();
      await SharePlus.instance.share(
        ShareParams(
          files: [XFile(file.path)],
          title: 'Резервная копия Изbrainого',
        ),
      );
    });
    if (mounted) setState(() => exporting = false);
  }

  Future<void> _restore() async {
    final selection = await safely(context, () => FilePicker.pickFile());
    if (selection == null || !mounted) return;
    final source = await copyPickedFile(selection, widget.brain.temporary);
    if (!mounted) return;
    final ok = await safely(context, () async {
      await widget.brain.restoreBackup(source);
      return true;
    });
    if (ok == true && mounted) notice(context, 'Архив восстановлен.');
  }

  Future<void> _code({bool connect = false}) async {
    final input = TextEditingController();
    bool busy = false;
    await showBrainDialog<void>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          title: Text(
            connect ? 'Подключить существующий архив' : 'Код подключения',
          ),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  connect
                      ? 'Введи код с другого устройства. Подключение возможно только к пустому архиву.'
                      : 'Этот код открывает твой архив. Сохрани его отдельно: он нужен для переноса и восстановления.',
                  style: const TextStyle(color: muted, height: 1.5),
                ),
                const SizedBox(height: 18),
                if (connect)
                  TextField(
                    controller: input,
                    autocorrect: false,
                    enableSuggestions: false,
                    decoration: const InputDecoration(labelText: 'Код архива'),
                  )
                else
                  SelectableText(
                    widget.brain.archiveCode,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 15,
                    ),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: busy ? null : () => Navigator.pop(c),
              child: const Text('Закрыть'),
            ),
            if (!connect)
              FilledButton.icon(
                onPressed: () => copySecret(c, widget.brain.archiveCode),
                icon: const Icon(Icons.copy),
                label: const Text('Скопировать'),
              ),
            if (connect)
              FilledButton(
                onPressed: busy
                    ? null
                    : () async {
                        set(() => busy = true);
                        final ok = await safely(c, () async {
                          await widget.brain.connectArchive(input.text);
                          return true;
                        });
                        if (!c.mounted) return;
                        if (ok == true) {
                          Navigator.pop(c);
                        } else {
                          set(() => busy = false);
                        }
                      },
                child: const Text('Подключить'),
              ),
          ],
        ),
      ),
    );
    input.clear();
    input.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.brain,
    builder: (context, _) {
      final brain = widget.brain;
      return Scaffold(
        appBar: AppBar(title: const Text('Настройки')),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 35),
          children: [
            FilledButton.icon(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => SetupScannerPage(brain: brain),
                ),
              ),
              icon: const Icon(Icons.qr_code_scanner),
              label: const Text('Перенести с Mac'),
            ),
            const SizedBox(height: 18),
            SurfaceBox(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Твоё устройство',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                  const SizedBox(height: 17),
                  TextField(
                    controller: name,
                    decoration: const InputDecoration(labelText: 'Название'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            SurfaceBox(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Gemini через Provod',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w500),
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'Фото и документы анализируются через твой API-ключ. Пароли из хранилища туда не отправляются.',
                    style: TextStyle(color: muted, fontSize: 13, height: 1.5),
                  ),
                  const SizedBox(height: 18),
                  TextField(
                    controller: key,
                    obscureText: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    decoration: const InputDecoration(
                      labelText: 'API-ключ Provod',
                    ),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: limit,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Лимит в месяц на этом устройстве, ₽',
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Оценка расхода за месяц: ${brain.usage.total.toStringAsFixed(2)} ₽',
                    style: const TextStyle(color: muted, fontSize: 12),
                  ),
                  const SizedBox(height: 5),
                  const Text(
                    'Лимит рассчитан по оценке токенов; фактическое списание определяет Provod. На другом устройстве учёт отдельный.',
                    style: TextStyle(color: muted, fontSize: 11, height: 1.4),
                  ),
                  ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    title: const Text(
                      'Дополнительно',
                      style: TextStyle(fontSize: 13),
                    ),
                    children: [
                      TextField(
                        controller: model,
                        autocorrect: false,
                        enableSuggestions: false,
                        decoration: const InputDecoration(labelText: 'Модель'),
                      ),
                      const SizedBox(height: 12),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            SurfaceBox(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          'Синхронизация',
                          style: TextStyle(
                            fontSize: 20,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                      if (brain.syncing)
                        const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text(
                    brain.syncStatus,
                    style: const TextStyle(color: accent, fontSize: 13),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Яндекс Диск используется для передачи. Облачная копия оригинала удаляется после проверки сохранения на всех подключённых устройствах — минимум на двух.',
                    style: TextStyle(color: muted, fontSize: 13, height: 1.5),
                  ),
                  const SizedBox(height: 18),
                  TextField(
                    controller: token,
                    obscureText: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    enabled: loaded,
                    decoration: const InputDecoration(
                      labelText: 'Токен Яндекс Диска',
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextButton(
                    onPressed: () => showBrainDialog<void>(
                      context: context,
                      builder: (c) => AlertDialog(
                        title: const Text('Подключение Диска'),
                        content: const Text(
                          'Для синхронизации нужен OAuth-токен с доступом к папке приложения на Яндекс Диске. На обоих устройствах должны совпадать код архива и аккаунт Диска.\n\nОригиналы остаются на устройствах. Диск служит передачей и не заменяет резервную копию.',
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => launchUrl(
                              Uri.parse('https://yandex.ru/dev/disk/rest/'),
                              mode: LaunchMode.externalApplication,
                            ),
                            child: const Text('Открыть страницу Диска'),
                          ),
                          TextButton(
                            onPressed: () => Navigator.pop(c),
                            child: const Text('Понятно'),
                          ),
                        ],
                      ),
                    ),
                    child: const Text('Как подключить'),
                  ),
                  if (brain.error != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      brain.error!,
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
                  ],
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: brain.syncing
                        ? null
                        : () => safely(context, brain.sync),
                    icon: const Icon(Icons.sync),
                    label: const Text('Синхронизировать сейчас'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: loaded && !saving ? _save : null,
              icon: saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.check),
              label: const Text('Сохранить и подключить'),
            ),
            const SizedBox(height: 24),
            SurfaceBox(
              padding: EdgeInsets.zero,
              child: Column(
                children: [
                  ListTile(
                    leading: const Icon(Icons.devices_outlined, color: accent),
                    title: const Text('Код подключения'),
                    subtitle: const Text(
                      'Для второго устройства и восстановления',
                      style: TextStyle(fontSize: 12, color: muted),
                    ),
                    onTap: () => _code(),
                  ),
                  ListTile(
                    leading: const Icon(Icons.link, color: accent),
                    title: const Text('Подключить существующий архив'),
                    onTap: () => _code(connect: true),
                  ),
                  ListTile(
                    leading: const Icon(Icons.backup_outlined, color: accent),
                    title: Text(
                      exporting
                          ? 'Подготовка копии…'
                          : 'Создать резервную копию',
                    ),
                    subtitle: const Text(
                      'Зашифрованные файлы и записи',
                      style: TextStyle(fontSize: 12, color: muted),
                    ),
                    enabled: !exporting,
                    onTap: _backup,
                  ),
                  ListTile(
                    leading: const Icon(Icons.restore, color: accent),
                    title: const Text('Восстановить резервную копию'),
                    onTap: _restore,
                  ),
                  ListTile(
                    leading: const Icon(Icons.delete_outline, color: muted),
                    title: const Text('Корзина'),
                    trailing: Text(
                      '${brain.trash.length}',
                      style: const TextStyle(color: muted),
                    ),
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => TrashPage(brain: brain),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 22),
            const Text(
              'Изbrainое · Android 0.1\nАрхив хранится на устройстве. Обработка очереди и синхронизация продолжаются при открытии приложения. Для анализа нужен интернет.',
              textAlign: TextAlign.center,
              style: TextStyle(color: muted, fontSize: 11, height: 1.6),
            ),
          ],
        ),
      );
    },
  );
}

class TrashPage extends StatelessWidget {
  final BrainController brain;
  const TrashPage({super.key, required this.brain});
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: brain,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Корзина')),
      body: brain.trash.isEmpty
          ? const EmptyState(
              icon: Icons.delete_outline,
              title: 'Корзина пуста',
              message: 'Удалённые материалы можно будет восстановить здесь.',
            )
          : ListView(
              padding: const EdgeInsets.all(18),
              children: brain.trash
                  .map(
                    (r) => MaterialTile(
                      record: r,
                      trailing: IconButton(
                        tooltip: 'Восстановить',
                        onPressed: () => safely(
                          context,
                          () => brain.trashRecord(r, restore: true),
                        ),
                        icon: const Icon(Icons.restore, color: accent),
                      ),
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
