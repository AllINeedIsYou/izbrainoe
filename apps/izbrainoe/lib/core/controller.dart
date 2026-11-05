import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;

import 'archive_store.dart';
import 'backup.dart';
import 'crypto_box.dart';
import 'intelligence.dart';
import 'relay.dart';
import 'setup_transfer.dart';
import 'previews.dart';
import 'photo_layout.dart';

abstract class SecretStore {
  Future<String?> read(String name);
  Future<void> write(String name, String value);
}

class DeviceSecrets implements SecretStore {
  final storage = const FlutterSecureStorage();
  @override
  Future<String?> read(String name) => storage.read(key: name);
  @override
  Future<void> write(String name, String value) =>
      storage.write(key: name, value: value);
}

class BrainController extends ChangeNotifier {
  final Directory root, temporary;
  final SecretStore secrets;
  late ArchiveStore archive;
  late UsageLedger usage;
  late GeminiClient ai;
  late String archiveCode, deviceId;
  Map<String, dynamic> settings = {};
  String? error;
  String syncStatus = 'Локальный архив';
  bool syncing = false, disposed = false, paused = false;
  int importing = 0;
  final Map<String, Future<Uint8List?>> _previews = {};
  final Map<String, Uint8List> _previewBytes = {};
  final Map<String, double> _photoAspects = {};
  final Queue<String> _queue = Queue();
  final Set<String> _scheduled = {}, _active = {}, _again = {};
  bool clearing = false, reviewingRule = false;
  int _generation = 0;
  String ruleReviewStatus = '';
  int _workers = 0;
  Timer? _syncTimer, _periodic, _vaultTimer;
  Map<String, dynamic>? _vault;
  String? _vaultPassword;
  RelaySync? _relay;
  final Serial _imports = Serial(),
      _vaultWrites = Serial(),
      _previewJobs = Serial();
  BrainController(this.root, this.temporary, this.secrets);
  void changed() {
    if (!disposed) notifyListeners();
  }

  String get deviceName =>
      settings['device_name'] as String? ??
      (Platform.isAndroid ? 'Android' : 'Mac');
  List<ArchiveRecord> get materials => archive.records
      .where(
        (r) =>
            !r.credential &&
            !['rule', 'vault', 'folder'].contains(r.kind) &&
            !r.deleted,
      )
      .toList();
  List<ArchiveRecord> get trash => archive.records
      .where((r) => !['vault', 'rule', 'folder'].contains(r.kind) && r.deleted)
      .toList();
  List<String> get folders => (<String>{
    ...materials.map((r) => r.folder),
    ...archive.records
        .where((r) => r.kind == 'folder' && !r.deleted)
        .map((r) => r.folder),
  }.toList()..sort((a, b) => a.compareTo(b)));
  List<ArchiveRecord> get folderDefinitions =>
      archive.records.where((r) => r.kind == 'folder' && !r.deleted).toList();
  List<String> get pinnedFolders =>
      (settings['pinned_folders'] as List? ?? folders.take(2).toList())
          .whereType<String>()
          .where(folders.contains)
          .take(4)
          .toList();
  List<Map<String, dynamic>> get routingRules {
    final rules =
        folderDefinitions
            .where((r) => (r.data['description'] as String? ?? '').isNotEmpty)
            .toList()
          ..sort(
            (a, b) => (b.data['rule_priority'] as int? ?? b.current.created)
                .compareTo(
                  a.data['rule_priority'] as int? ?? a.current.created,
                ),
          );
    return rules
        .map(
          (r) => {
            'folder': r.folder,
            'description': safeText(r.data['description'] as String),
          },
        )
        .toList();
  }

  String get _ruleStamp => routingRules.toString();
  Future<void> preferences(Map<String, dynamic> patch) async {
    settings.addAll(patch);
    await atomicWrite(
      File(p.join(archive.directory.path, 'profile.enc')),
      await archive.cipher.sealJson(settings, 'profile'),
    );
    changed();
  }

  Future<void> pinFolder(String folder) async {
    final pins = pinnedFolders.toList();
    if (pins.contains(folder)) {
      pins.remove(folder);
    } else {
      if (pins.length >= 4) {
        throw const FormatException(
          'Можно закрепить до четырёх папок. Сначала открепи одну.',
        );
      }
      pins.add(folder);
    }
    await preferences({'pinned_folders': pins});
  }

  bool folderGrid(String folder) =>
      (settings['file_views'] as Map? ?? {})[folder] != 'list';
  Future<void> setFolderGrid(String folder, bool grid) => preferences({
    'file_views': {
      ...settings['file_views'] as Map? ?? {},
      folder: grid ? 'grid' : 'list',
    },
  });

  int get pending => _scheduled.length;
  bool get vaultOpen => _vault != null;
  ArchiveRecord? get vaultRecord =>
      archive.records.where((r) => r.kind == 'vault' && !r.deleted).firstOrNull;
  List<Map<String, dynamic>> get credentials =>
      (_vault?['entries'] as List? ?? [])
          .map((e) => Map<String, dynamic>.from(e as Map))
          .where((e) => e['deleted'] != true)
          .toList();

  Future<void> initialize() async {
    await root.create(recursive: true);
    await temporary.create(recursive: true);
    // Wipe app-private decrypted previews left by a previous process.
    await for (final f in temporary.list()) {
      if (f is File && !p.basename(f.path).endsWith('.izbrainoe')) {
        try {
          await f.delete();
        } catch (_) {}
      }
    }
    archiveCode =
        await secrets.read('archive_code') ?? ArchiveCipher.createCode();
    await secrets.write('archive_code', archiveCode);
    deviceId = await secrets.read('device_id') ?? newId();
    await secrets.write('device_id', deviceId);
    await _openArchive();
    _periodic = Timer.periodic(const Duration(minutes: 2), (_) {
      if (!paused) unawaited(sync());
    });
  }

  Future<void> _openArchive() async {
    final cipher = await ArchiveCipher.fromCode(archiveCode);
    final directory = Directory(p.join(root.path, cipher.archiveId));
    archive = ArchiveStore(directory, cipher, deviceId);
    await archive.load();
    _previews.clear();
    _previewBytes.clear();
    _photoAspects.clear();
    await _ensurePhotoLayouts();
    final profile = File(p.join(directory.path, 'profile.enc'));
    settings = await profile.exists()
        ? await cipher.openJson(await profile.readAsBytes(), 'profile')
        : {};
    usage = UsageLedger(File(p.join(directory.path, 'usage.enc')), cipher);
    await usage.load();
    ai = GeminiClient(
      usage,
      apiKey: await secrets.read('provod_key') ?? '',
      model: settings['model'] as String? ?? 'gemini-3.8-flash',
      monthlyLimit: (settings['limit_rub'] as num? ?? 1000).toDouble(),
    );
    await _configureRelay();
    if (ai.enabled) {
      for (final item in materials.where(
        (r) => [
          'pending',
          'analyzing',
          'retry',
        ].contains(r.data['analysis_state']),
      )) {
        schedule(item.id);
      }
    }
    changed();
  }

  Future<void> _configureRelay() async {
    _relay?.remote.close();
    _relay = null;
    final token = await secrets.read('disk_token');
    if (token != null && token.isNotEmpty) {
      _relay = RelaySync(
        archive,
        YandexDisk(token, archive.cipher.archiveId),
        deviceName,
      );
      syncStatus = 'Готов к синхронизации';
    }
  }

  Future<void> saveSettings({
    required String key,
    required String token,
    required String name,
    required String model,
    required double limit,
  }) async {
    if (limit < 0 || !limit.isFinite) {
      throw const FormatException('Укажи корректный лимит.');
    }
    await secrets.write('provod_key', key.trim());
    await secrets.write('disk_token', token.trim());
    settings.addAll({
      'device_name': cut(name.trim().isEmpty ? deviceName : name.trim(), 60),
      'model': model.trim().isEmpty ? 'gemini-3.8-flash' : model.trim(),
      'limit_rub': limit,
    });
    await atomicWrite(
      File(p.join(archive.directory.path, 'profile.enc')),
      await archive.cipher.sealJson(settings, 'profile'),
    );
    ai.apiKey = key.trim();
    ai.model = settings['model'] as String;
    ai.monthlyLimit = limit;
    await _configureRelay();
    for (final r in materials.where(
      (r) => r.data['analysis_state'] != 'done',
    )) {
      schedule(r.id);
    }
    kickSync();
    changed();
  }

  Future<void> connectArchive(String code) async {
    if (archive.revisions.isNotEmpty) {
      throw const FormatException(
        'Подключать другой архив можно на пустом устройстве. Текущий архив не заменён.',
      );
    }
    if (syncing || _workers > 0 || importing > 0) {
      throw const FormatException('Дождись завершения текущих действий.');
    }
    await ArchiveCipher.fromCode(code);
    lockVault();
    ai.client.close();
    archiveCode = code.trim();
    await secrets.write('archive_code', archiveCode);
    await _openArchive();
    kickSync();
  }

  Future<void> applySetup(
    Map<String, dynamic> setup, {
    void Function(String phase, double? progress)? onProgress,
  }) async {
    final code = setup['archive_code'] as String;
    final incoming = await ArchiveCipher.fromCode(code);
    final changing = incoming.archiveId != archive.cipher.archiveId;
    if (changing && archive.revisions.isNotEmpty) {
      throw const FormatException(
        'На телефоне уже есть другой архив. Перенос не заменит его. Сначала сохрани резервную копию текущего архива.',
      );
    }
    if (syncing || _workers > 0 || importing > 0) {
      throw const FormatException(
        'Дождись завершения текущих действий и повтори перенос.',
      );
    }
    onProgress?.call('Подключаюсь к Mac…', null);
    final file = await downloadSetupArchive(
      setup,
      temporary,
      onProgress: (received, total) => onProgress?.call(
        'Получаю архив с Mac: ${(received / 1048576).toStringAsFixed(1)} МБ'
        '${total > 0 ? " из ${(total / 1048576).toStringAsFixed(1)} МБ" : ""}',
        total > 0 ? received / total : null,
      ),
    );
    try {
      if (changing) await connectArchive(code);
      if (file != null) {
        onProgress?.call('Проверяю и сохраняю файлы…', null);
        await ArchiveBackup.import(
          file,
          archive,
          onProgress: (done, total) =>
              onProgress?.call('Проверяю и сохраняю файлы…', done / total),
        );
      }
      await _ensurePhotoLayouts();
      onProgress?.call('Подключаю Gemini…', null);
      await saveSettings(
        key: setup['provod_key'] as String,
        token:
            setup['disk_token'] as String? ??
            await secrets.read('disk_token') ??
            '',
        name: deviceName,
        model: setup['model'] as String? ?? 'gemini-3.8-flash',
        limit: (setup['limit_rub'] as num? ?? 1000).toDouble(),
      );
    } finally {
      if (file != null && await file.exists()) await file.delete();
    }
  }

  void kickSync() {
    _syncTimer?.cancel();
    _syncTimer = Timer(const Duration(seconds: 2), () {
      if (!paused) unawaited(sync());
    });
  }

  Future<void> sync() async {
    if (disposed || syncing || _relay == null) return;
    syncing = true;
    syncStatus = 'Синхронизация…';
    changed();
    try {
      final before = vaultRecord?.heads.map((r) => r.id).join(',');
      final result = await _relay!.sync();
      if (result.downloaded > 0) {
        _previews.clear();
        await _ensurePhotoLayouts();
      }
      if (vaultOpen &&
          before != vaultRecord?.heads.map((r) => r.id).join(',')) {
        lockVault();
      }
      syncStatus = result.waiting > 0
          ? 'Ожидают передачи: ${result.waiting}'
          : 'Синхронизировано';
      error = null;
      // Remote analysis is reused. Receiving a file never starts a paid reanalysis.
    } catch (e) {
      syncStatus = 'Синхронизация отложена';
      error = friendly(e);
    } finally {
      syncing = false;
      changed();
    }
  }

  Future<ArchiveRecord> addNote(String body, {String context = ''}) async {
    if (clearing) throw const FormatException('Дождись очистки.');
    final text = body.trim();
    if (text.length > 200000) {
      throw const FormatException(
        'Заметка слишком длинная. Сохрани её текстовым файлом.',
      );
    }
    if (text.isEmpty) throw const FormatException('Напиши заметку.');
    final sensitive = RegExp(
      r'(пароль|password|api[_ -]?key|токен)\s*[:=]',
      caseSensitive: false,
    ).hasMatch(text);
    final record = await archive.save({
      'title': cut(text.split('\n').first, 100),
      'body': text,
      'kind': Uri.tryParse(text)?.hasScheme == true ? 'link' : 'note',
      'context': context,
      'folder': sensitive
          ? 'Личное'
          : fallbackFolder('', body: text, context: context),
      'sensitive': sensitive,
      'analysis_state': sensitive ? 'private' : 'pending',
    });
    if (!sensitive) schedule(record.id);
    kickSync();
    changed();
    return record;
  }

  Future<ArchiveRecord> addFile(
    File source, {
    String? name,
    String context = '',
    String? folder,
  }) => _imports.run(() async {
    if (clearing) throw const FormatException('Дождись очистки.');
    importing++;
    changed();
    try {
      final filename = p.basename(name ?? source.path),
          blob = await archive.importFile(source);
      final duplicate = materials
          .where(
            (r) =>
                r.blob?['hash'] == blob['hash'] &&
                r.data['filename'] == filename,
          )
          .firstOrNull;
      if (duplicate != null) {
        if (context.isNotEmpty || folder != null) {
          await addContext(duplicate, context, folder: folder);
        }
        return archive.get(duplicate.id)!;
      }
      double? aspect;
      if (kindFor(filename) == 'photo') {
        final input = await source.open();
        try {
          aspect = photoAspect(await input.read(1024 * 1024));
        } finally {
          await input.close();
        }
        if (aspect != null) _photoAspects[blob['hash'] as String] = aspect;
      }
      final record = await archive.save({
        'photo_aspect': ?aspect,
        'title': filename,
        'filename': filename,
        'kind': kindFor(filename),
        'blob': blob,
        'context': context,
        if (folder != null) 'manual_folder': true,
        'folder':
            (folder == null ? null : canonicalFolder(folder)) ??
            _rule(filename, context) ??
            fallbackFolder(filename, context: context),
        'analysis_state': 'pending',
      });
      schedule(record.id);
      kickSync();
      changed();
      return record;
    } finally {
      importing--;
      changed();
    }
  });
  String? _rule(String filename, String context) {
    if (context.trim().isNotEmpty) return null;
    final ext = p.extension(filename).toLowerCase();
    final definitions = folderDefinitions.toList()
      ..sort(
        (a, b) => (b.data['rule_priority'] as int? ?? b.current.created)
            .compareTo(a.data['rule_priority'] as int? ?? a.current.created),
      );
    for (final rule in definitions) {
      final extensions = (rule.data['extensions'] as List? ?? [])
          .whereType<String>();
      if (extensions.isNotEmpty) {
        if (extensions.contains(ext)) return rule.folder;
        continue;
      }
      // A newer semantic rule must be evaluated before any older format rule.
      if ((rule.data['description'] as String? ?? '').trim().isNotEmpty) {
        return null;
      }
    }
    return archive.records
        .where(
          (r) => r.kind == 'rule' && !r.deleted && r.data['extension'] == ext,
        )
        .firstOrNull
        ?.folder;
  }

  Future<void> createFolderRule(
    String folder,
    String description, {
    bool reviewExisting = false,
  }) async {
    final clean = canonicalFolder(folder.trim());
    if (folder.trim().isEmpty || clean == 'Неразобранное') {
      throw const FormatException('Укажи название подборки.');
    }
    if (description.length > 3000) {
      throw const FormatException('Сократи правило до 3000 символов.');
    }
    if (reviewingRule) {
      throw const FormatException('Дождись текущего пересмотра папок.');
    }
    var ruleText = description.trim();
    final extensionOnly =
        RegExp(r'^\.?[a-zA-Z0-9]{1,12}(?:[,;\s]+\.?[a-zA-Z0-9]{1,12})*$')
            .hasMatch(ruleText) &&
        ruleText
            .toLowerCase()
            .split(RegExp(r'[,;\s]+'))
            .every(
              (v) =>
                  v.startsWith('.') ||
                  {
                    'mp3',
                    'flac',
                    'wav',
                    'ogg',
                    'opus',
                    'mp4',
                    'mov',
                    'mkv',
                    'zip',
                    'rar',
                    '7z',
                    'pdf',
                    'docx',
                    'xlsx',
                    'pptx',
                    'jpg',
                    'jpeg',
                    'png',
                    'json',
                    'txt',
                    'py',
                    'js',
                  }.contains(v),
            );
    final extensions = extensionOnly
        ? ruleText
              .toLowerCase()
              .split(RegExp(r'[,;\s]+'))
              .map((v) => v.startsWith('.') ? v : '.$v')
              .toSet()
              .toList()
        : <String>[];
    if (extensions.isNotEmpty) {
      ruleText = 'Все файлы форматов ${extensions.join(", ")}';
    }
    final existing = folderDefinitions
        .where((r) => r.folder == clean)
        .firstOrNull;
    final definition = await archive.save(
      {
        ...?existing?.data,
        'kind': 'folder',
        'title': clean,
        'folder': clean,
        'description': ruleText,
        'extensions': extensions,
        'rule_priority': DateTime.now().microsecondsSinceEpoch,
        'deleted': false,
      },
      recordId: existing?.id,
      expectedHeads: existing?.heads.map((r) => r.id).toList(),
    );
    for (final old
        in archive.records
            .where(
              (r) =>
                  r.kind == 'rule' &&
                  !r.deleted &&
                  r.folder == clean &&
                  !extensions.contains(r.data['extension']),
            )
            .toList()) {
      await archive.save(
        {...old.data, 'deleted': true},
        recordId: old.id,
        expectedHeads: old.heads.map((r) => r.id).toList(),
      );
    }
    for (final ext in extensions) {
      final previous = archive.records
          .where((r) => r.kind == 'rule' && r.data['extension'] == ext)
          .firstOrNull;
      await archive.save({
        'kind': 'rule',
        'title': 'Правило $ext',
        'folder': clean,
        'extension': ext,
        'deleted': false,
      }, recordId: previous?.id);
    }
    changed();
    kickSync();
    if (!reviewExisting || ruleText.isEmpty) return;
    reviewingRule = true;
    ruleReviewStatus = 'Пересматриваю сохранённые материалы…';
    changed();
    try {
      final candidates = materials
          .where(
            (r) =>
                !r.credential &&
                !r.conflict &&
                r.data['sensitive'] != true &&
                r.folder != clean,
          )
          .toList();
      var moved = 0, checked = 0;
      final semantic = <ArchiveRecord>[];
      for (final r in candidates) {
        final ext = p
            .extension(r.data['filename'] as String? ?? '')
            .toLowerCase();
        if (extensions.isNotEmpty) {
          if (extensions.contains(ext)) {
            await _moveByRule(r, definition);
            moved++;
          }
        } else if (r.kind != 'video' ||
            (r.data['context'] as String? ?? '').trim().isNotEmpty) {
          if ((r.data['summary'] as String? ?? '').isEmpty &&
              (r.data['text'] as String? ?? '').isEmpty &&
              (r.data['body'] as String? ?? '').isEmpty &&
              r.kind == 'photo') {
            schedule(r.id);
          } else {
            semantic.add(r);
          }
        }
      }
      if (semantic.isNotEmpty && !ai.enabled) {
        throw const FormatException(
          'Правило сохранено. Для пересмотра старых материалов подключи Gemini в настройках.',
        );
      }
      for (var offset = 0; offset < semantic.length; offset += 20) {
        final batch = semantic.skip(offset).take(20).toList();
        final ids = await ai.matchFolderRule(ruleText, batch);
        for (final id in ids) {
          final old = batch.firstWhere((r) => r.id == id),
              live = archive.get(id);
          if (live != null &&
              !live.deleted &&
              live.heads.map((r) => r.id).join() ==
                  old.heads.map((r) => r.id).join()) {
            await _moveByRule(live, definition);
            moved++;
          }
        }
        checked += batch.length;
        ruleReviewStatus =
            'Проверено: $checked из ${semantic.length}. Перемещено: $moved';
        changed();
      }
      ruleReviewStatus = 'Готово. Перемещено: $moved';
    } finally {
      reviewingRule = false;
      changed();
      kickSync();
    }
  }

  Future<void> _moveByRule(ArchiveRecord record, ArchiveRecord rule) => archive
      .save(
        {
          ...record.data,
          'folder': rule.folder,
          'manual_folder': false,
          'matched_rule': rule.id,
        },
        recordId: record.id,
        expectedHeads: record.heads.map((r) => r.id).toList(),
      )
      .then((_) {});

  Future<void> trashMany(Iterable<String> ids) async {
    for (final id in ids.toSet()) {
      final r = archive.get(id);
      if (r != null && !r.deleted && !r.credential && r.kind != 'vault') {
        await trashRecord(r);
      }
    }
  }

  Future<void> deleteFolder(String folder) async {
    if (reviewingRule) {
      throw const FormatException('Дождись пересмотра материалов.');
    }
    for (final r in materials.where((r) => r.folder == folder).toList()) {
      await trashRecord(r);
    }
    for (final r
        in archive.records
            .where(
              (r) =>
                  ['folder', 'rule'].contains(r.kind) &&
                  r.folder == folder &&
                  !r.deleted,
            )
            .toList()) {
      await archive.save(
        {...r.data, 'deleted': true},
        recordId: r.id,
        expectedHeads: r.heads.map((r) => r.id).toList(),
      );
    }
    await preferences({
      'pinned_folders': pinnedFolders.where((v) => v != folder).toList(),
    });
    kickSync();
    changed();
  }

  Future<void> clearAll() async {
    if (clearing || importing > 0 || syncing || reviewingRule) {
      throw const FormatException(
        'Дождись завершения текущих действий перед очисткой.',
      );
    }
    clearing = true;
    paused = true;
    _generation++;
    _queue.clear();
    _again.clear();
    _syncTimer?.cancel();
    lockVault();
    ai.client.close();
    _relay?.remote.close();
    _relay = null;
    changed();
    try {
      while (_workers > 0) {
        await Future<void>.delayed(const Duration(milliseconds: 30));
      }
      _scheduled.clear();
      _previews.clear();
      _previewBytes.clear();
      _photoAspects.clear();
      for (final name in [
        'provod_key',
        'disk_token',
        'archive_code',
        'device_id',
      ]) {
        await secrets.write(name, '');
      }
      if (await root.exists()) await root.delete(recursive: true);
      if (await temporary.exists()) await temporary.delete(recursive: true);
      await root.create(recursive: true);
      await temporary.create(recursive: true);
      archiveCode = ArchiveCipher.createCode();
      deviceId = newId();
      await secrets.write('archive_code', archiveCode);
      await secrets.write('device_id', deviceId);
      await _openArchive();
      error = null;
      ruleReviewStatus = '';
      syncStatus = 'Локальный архив';
    } finally {
      clearing = false;
      paused = false;
      changed();
    }
  }

  void schedule(String id) {
    if (disposed || clearing) return;
    if (!_scheduled.add(id)) {
      if (_active.contains(id)) _again.add(id);
      return;
    }
    _queue.add(id);
    _pump();
  }

  void _pump() {
    while (!disposed &&
        !clearing &&
        !paused &&
        _workers < 2 &&
        _queue.isNotEmpty) {
      final id = _queue.removeFirst();
      _workers++;
      _active.add(id);
      unawaited(
        _analyze(id).whenComplete(() {
          _workers--;
          _scheduled.remove(id);
          _active.remove(id);
          if (_again.remove(id) && !clearing) schedule(id);
          changed();
          _pump();
        }),
      );
    }
    changed();
  }

  Future<void> _analyze(String id) async {
    File? clear;
    final generation = _generation;
    final ruleStamp = _ruleStamp;
    try {
      var record = archive.get(id);
      if (record == null ||
          record.deleted ||
          record.conflict ||
          record.credential ||
          record.kind == 'vault' ||
          record.data['sensitive'] == true) {
        return;
      }
      if (record.kind == 'video') {
        final context = record.data['context'] as String? ?? '';
        final before = record.heads.map((r) => r.id).toList();
        var folder = record.data['manual_folder'] == true
            ? record.folder
            : 'Видео';
        if (context.isNotEmpty && record.data['manual_folder'] != true) {
          folder =
              folders
                  .where((f) => context.toLowerCase().contains(f.toLowerCase()))
                  .firstOrNull ??
              'Видео';
          if (ai.enabled) {
            final answer = await ai.ask(
              'Выбери ровно одну папку для пояснения владельца к видео. Самого видео у тебя нет; не делай выводов о его содержимом сверх пояснения. Новые явные routing_rules_newest_first важнее старых папок, при нескольких подходящих выбирай первое. Используй существующее название, когда оно подходит. Верни только folder.',
              {
                'context': safeText(context),
                'existing_folders': folders,
                'routing_rules_newest_first': routingRules,
              },
              {
                'type': 'object',
                'additionalProperties': false,
                'required': ['folder'],
                'properties': {
                  'folder': {'type': 'string'},
                },
              },
              cap: 200,
            );
            if (answer['folder'] is! String) {
              throw AiFailure('Не удалось выбрать папку по пояснению.');
            }
            folder = canonicalFolder(answer['folder'] as String);
          }
        }
        final live = archive.get(id);
        if (clearing ||
            generation != _generation ||
            live == null ||
            live.deleted ||
            live.heads.map((r) => r.id).join() != before.join()) {
          return;
        }
        if (ruleStamp != _ruleStamp) {
          _again.add(id);
          return;
        }
        await archive.save(
          {
            ...record.data,
            'folder': folder,
            'summary': context.isEmpty ? 'Сохранённое видео.' : context,
            'text': context,
            'analysis_state': 'done',
            'reading_note': context.isEmpty
                ? 'Видео сохранено по формату без расхода Gemini.'
                : 'Само видео не отправляется в Gemini. Для выбора папки используется только твоё текстовое пояснение.',
          },
          recordId: id,
          expectedHeads: before,
        );
        kickSync();
        return;
      }
      final before = record.heads.map((r) => r.id).toList();
      final extensionFolder = _rule(
        record.data['filename'] as String? ?? '',
        record.data['context'] as String? ?? '',
      );
      if (extensionFolder != null) {
        await archive.save(
          {
            ...record.data,
            'folder': record.data['manual_folder'] == true
                ? record.folder
                : extensionFolder,
            'analysis_state': 'done',
            'reading_note': 'Применено правило формата без расхода Gemini.',
          },
          recordId: id,
          expectedHeads: before,
        );
        kickSync();
        return;
      }
      final defaultFolder =
          _rule(
            record.data['filename'] as String? ?? '',
            record.data['context'] as String? ?? '',
          ) ??
          fallbackFolder(record.data['filename'] as String? ?? '');
      if (routingRules.isEmpty &&
          (record.data['context'] as String? ?? '').isEmpty &&
          ['Музыка', 'Видео', 'ZIP'].contains(defaultFolder)) {
        String text = '';
        if (defaultFolder == 'ZIP' &&
            record.blob != null &&
            await archive.hasBlob(record.blob!)) {
          clear = await archive.materialize(
            record.blob!,
            temporary,
            record.data['filename'] as String? ?? 'archive.zip',
          );
          text = (await prepareFile(
            clear,
            record.data['filename'] as String? ?? 'archive.zip',
          )).text;
        }
        await archive.save(
          {
            ...record.data,
            'folder': record.data['manual_folder'] == true
                ? record.folder
                : defaultFolder,
            'text': text,
            'analysis_state': 'done',
            'summary': defaultFolder == 'ZIP'
                ? 'Архив файлов.'
                : defaultFolder == 'Музыка'
                ? 'Сохранённый аудиофайл.'
                : 'Сохранённый видеофайл.',
            'reading_note': 'Сортировка по формату и названию без расхода Gemini. Содержимое аудио и видео не распознаётся.',
          },
          recordId: id,
          expectedHeads: before,
        );
        kickSync();
        return;
      }
      PreparedFile prepared;
      if (record.blob != null) {
        if (!await archive.hasBlob(record.blob!)) return;
        clear = await archive.materialize(
          record.blob!,
          temporary,
          record.data['filename'] as String? ?? 'file',
        );
        prepared = await prepareFile(
          clear,
          record.data['filename'] as String? ?? 'file',
        ).timeout(const Duration(seconds: 30));
      } else {
        prepared = PreparedFile(
          text: cut(
            safeText(
              '${record.data['body'] ?? ''}\n${record.data['text'] ?? ''}',
            ),
            16000,
          ),
          note: 'Текстовая заметка.',
        );
      }
      final examples = materials
          .where(
            (r) =>
                r.id != id &&
                (r.data['manual_folder'] == true ||
                    (r.data['context'] as String? ?? '').isNotEmpty),
          )
          .take(8)
          .map(
            (r) => {
              'title': cut(safeText(r.title), 100),
              'context': cut(safeText(r.data['context'] as String? ?? ''), 200),
              'folder': r.folder,
            },
          )
          .toList();
      if (!ai.enabled) {
        final live = archive.get(id);
        if (live == null ||
            live.heads.map((r) => r.id).join() != before.join()) {
          return;
        }
        await archive.save(
          {
            ...record.data,
            'folder': record.data['manual_folder'] == true
                ? record.folder
                : _rule(
                        record.data['filename'] as String? ?? '',
                        record.data['context'] as String? ?? '',
                      ) ??
                      fallbackFolder(
                        record.data['filename'] as String? ?? '',
                        body: prepared.text,
                        context: record.data['context'] as String? ?? '',
                      ),
            'text': prepared.text,
            'reading_note': prepared.note,
            'analysis_state': 'local',
          },
          recordId: id,
          expectedHeads: before,
        );
        return;
      }
      final answer = await ai.analyze(
        record.data,
        prepared,
        folders,
        examples,
        rules: routingRules,
      );
      if (clearing || generation != _generation) return;
      if (ruleStamp != _ruleStamp) {
        _again.add(id);
        return;
      }
      final live = archive.get(id);
      if (live == null ||
          live.deleted ||
          live.heads.map((r) => r.id).join() != before.join()) {
        return;
      }
      var folder = record.data['manual_folder'] == true
          ? record.folder
          : _rule(
                  record.data['filename'] as String? ?? '',
                  record.data['context'] as String? ?? '',
                ) ??
                answer['folder'] as String;
      folder =
          folders
              .where((f) => f.toLowerCase() == folder.toLowerCase())
              .firstOrNull ??
          folder;
      final noEyes =
          (record.kind == 'photo' ||
              p
                      .extension(record.data['filename'] as String? ?? '')
                      .toLowerCase() ==
                  '.pdf') &&
          prepared.images.isEmpty;
      await archive.save(
        {
          ...record.data,
          'folder': folder,
          'summary': answer['summary'],
          'text': cut(
            '${prepared.text.isEmpty ? record.data['text'] ?? '' : prepared.text}\n${answer['recognized_text']}',
            18000,
          ),
          'topics': answer['topics'],
          'document_type': answer['document_type'],
          'visual_summary': answer['visual_summary'],
          'confidence': answer['confidence'],
          'needs_context': answer['needs_context'] == true || noEyes,
          'reading_note': prepared.note,
          'sources': answer['sources'] ?? [],
          'analysis_state': 'done',
          'analysis_error': '',
        },
        recordId: id,
        expectedHeads: before,
      );
      kickSync();
    } catch (e) {
      final record = archive.get(id);
      if (!clearing &&
          generation == _generation &&
          record != null &&
          !record.deleted &&
          !record.conflict) {
        try {
          await archive.save(
            {
              ...record.data,
              'analysis_state': 'retry',
              'analysis_error': friendly(e),
            },
            recordId: id,
            expectedHeads: record.heads.map((r) => r.id).toList(),
          );
        } catch (_) {}
      }
    } finally {
      if (clear != null && await clear.exists()) await clear.delete();
    }
  }

  Future<void> addContext(
    ArchiveRecord record,
    String context, {
    String? folder,
  }) async {
    if (RegExp(
      r'^удалить[.!\s]*$',
      caseSensitive: false,
    ).hasMatch(context.trim())) {
      await trashRecord(record);
      return;
    }
    final next = await archive.save(
      {
        ...record.data,
        'context': context.trim(),
        if (folder != null) 'folder': canonicalFolder(folder),
        if (folder != null) 'manual_folder': true,
        'analysis_state': 'pending',
      },
      recordId: record.id,
      expectedHeads: record.heads.map((r) => r.id).toList(),
    );
    schedule(next.id);
    if (record.kind == 'video') {
      kickSync();
      changed();
      return;
    }
    // Bounded broad reconsideration: same formats plus related text, never a mass
    // reclassification of manual choices or secret records.
    final words = context
        .toLowerCase()
        .split(RegExp(r'[^a-zа-яё0-9]+'))
        .where((x) => x.length > 3)
        .toSet();
    final ext = p
        .extension(record.data['filename'] as String? ?? '')
        .toLowerCase();
    final candidates = materials
        .where(
          (r) =>
              r.id != record.id &&
              r.data['manual_folder'] != true &&
              r.data['sensitive'] != true,
        )
        .toList();
    candidates.sort((a, b) {
      int score(ArchiveRecord r) =>
          (p.extension(r.data['filename'] as String? ?? '').toLowerCase() == ext
              ? 3
              : 0) +
          words
                  .where(
                    (w) => '${r.title} ${r.data['summary']} ${r.data['text']}'
                        .toLowerCase()
                        .contains(w),
                  )
                  .length *
              2;
      return score(b).compareTo(score(a));
    });
    for (final r in candidates.take(12)) {
      schedule(r.id);
    }
    kickSync();
    changed();
  }

  Future<void> trashRecord(ArchiveRecord record, {bool restore = false}) async {
    await archive.save(
      {...record.data, 'deleted': !restore},
      recordId: record.id,
      expectedHeads: record.heads.map((r) => r.id).toList(),
    );
    kickSync();
    changed();
  }

  Future<void> resolve(ArchiveRecord record, Revision selected) async {
    await archive.save(
      selected.data,
      recordId: record.id,
      expectedHeads: record.heads.map((r) => r.id).toList(),
    );
    kickSync();
    changed();
  }

  Future<List<ArchiveRecord>> search(String query) async {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return [];
    final lexical = materials
        .where(
          (r) =>
              '${r.title} ${r.folder} ${r.data['context']} ${r.data['summary']} ${r.data['text']}'
                  .toLowerCase()
                  .contains(q),
        )
        .toList();
    if (!ai.enabled) return lexical;
    // Rank cheap lexical candidates first; Gemini sees at most 200 concise records.
    final shortlist = [
      ...lexical,
      ...materials.where((r) => !lexical.any((l) => l.id == r.id)),
    ];
    try {
      final ids = await ai.search(query, shortlist);
      return ids.map(archive.get).whereType<ArchiveRecord>().toList();
    } catch (e) {
      error = friendly(e);
      changed();
      if (lexical.isNotEmpty) return lexical;
      rethrow;
    }
  }

  static Set<String> serviceWords(String text) =>
      RegExp(r'[\p{L}\p{N}_]+', unicode: true)
          .allMatches(text.toLowerCase().replaceAll('ё', 'е'))
          .map((m) => m.group(0)!)
          .where((w) => w.length > 1)
          .take(64)
          .toSet();
  bool credentialQuery(String query) {
    var q = query.trim().toLowerCase();
    final command = RegExp(
      r'^(?:данные|пароль|логин|найди|покажи)(?:\s+|$)',
      caseSensitive: false,
    );
    while (command.hasMatch(q)) {
      q = q.replaceFirst(command, '').trim();
    }
    final wanted = serviceWords(q);
    if (wanted.isEmpty) return false;
    final services = (vaultRecord?.data['services'] as List? ?? [])
        .whereType<String>();
    if (services.any((s) => serviceWords(s).containsAll(wanted))) return true;
    final hashes = wanted.map((w) => digest(utf8.encode(w))).toSet();
    final lookup = vaultRecord?.data['lookup_tokens'] as List? ?? [];
    return lookup.whereType<List>().any(
      (entry) => entry.whereType<String>().toSet().containsAll(hashes),
    );
  }

  void touchVault() {
    _vaultTimer?.cancel();
    if (vaultOpen) _vaultTimer = Timer(const Duration(minutes: 2), lockVault);
  }

  void lockVault() {
    _vault = null;
    _vaultPassword = null;
    _vaultTimer?.cancel();
    changed();
  }

  Future<void> unlockVault(String password) async {
    final record = vaultRecord;
    if (record == null) {
      if (password.runes.length < 8) {
        throw const FormatException('Главный пароль — минимум 8 символов.');
      }
      _vault = {'entries': <dynamic>[]};
      _vaultPassword = password;
    } else {
      final merged = <String, Map<String, dynamic>>{};
      for (final head in record.heads) {
        final clear = await PasswordEnvelope.decrypt(
          Map<String, dynamic>.from(head.data['envelope'] as Map),
          password,
        );
        for (final raw in clear['entries'] as List) {
          final e = Map<String, dynamic>.from(raw as Map),
              id = e['id'] as String;
          e['service'] ??= e['title'] ?? 'Без названия';
          e['note'] ??= e['notes'] ?? '';
          if (merged.containsKey(id) &&
              jsonEncode(merged[id]) != jsonEncode(e)) {
            final alternate = digest(utf8.encode('$id:${head.id}'))
                .substring(0, 32);
            merged[alternate] = {
              ...e,
              'id': alternate,
              'service': '${e['service']} (другая версия)',
            };
          } else {
            merged[id] = e;
          }
        }
      }
      if (record.heads.map((h) => h.id).join(',') !=
          vaultRecord?.heads.map((h) => h.id).join(',')) {
        throw const FormatException(
          'Хранилище изменилось на другом устройстве. Открой его заново.',
        );
      }
      _vault = {'entries': merged.values.toList()};
      _vaultPassword = password;
      if (!record.conflict &&
          (record.data['services'] as List? ?? []).isEmpty &&
          merged.isNotEmpty) {
        await archive.save(
          {
            ...record.data,
            'services': merged.values
                .where((e) => e['deleted'] != true)
                .map((e) => e['service'])
                .toList(),
          },
          recordId: record.id,
          expectedHeads: record.heads.map((h) => h.id).toList(),
        );
        kickSync();
      }
    }
    touchVault();
    changed();
  }

  Future<void> saveCredential({
    String? id,
    required String service,
    required String login,
    required String password,
    String note = '',
    bool deleted = false,
  }) => _vaultWrites.run(() async {
    if (_vault == null || _vaultPassword == null) {
      throw const FormatException('Сначала открой хранилище.');
    }
    if (service.trim().isEmpty) {
      throw const FormatException('Укажи название сервиса.');
    }
    final master = _vaultPassword!, record = vaultRecord;
    final entries = List<dynamic>.from(_vault!['entries'] as List),
        key = id ?? newId();
    final previous = entries
        .whereType<Map>()
        .where((e) => e['id'] == key)
        .firstOrNull;
    entries.removeWhere((e) => (e as Map)['id'] == key);
    entries.add({
      ...?previous,
      'id': key,
      'service': service.trim(),
      'login': login,
      'password': password,
      'note': note,
      'deleted': deleted,
    });
    final clear = <String, dynamic>{'entries': entries},
        envelope = await PasswordEnvelope.encrypt(clear, master);
    if (!vaultOpen || _vaultPassword != master) {
      throw const FormatException(
        'Хранилище заблокировано. Сохранение отменено.',
      );
    }
    await archive.save(
      {
        'kind': 'vault',
        'title': 'Хранилище паролей',
        'envelope': envelope,
        'lookup_tokens': entries
            .whereType<Map>()
            .where((e) => e['deleted'] != true)
            .map(
              (e) =>
                  serviceWords('${e['service']} ${e['aliases'] ?? ''}')
                      .map((w) => digest(utf8.encode(w)))
                      .toList(),
            )
            .toList(),
        'services': entries
            .where((e) => (e as Map)['deleted'] != true)
            .map((e) => (e as Map)['service'])
            .toList(),
      },
      recordId: record?.id,
      expectedHeads: record?.heads.map((r) => r.id).toList(),
    );
    _vault = clear;
    touchVault();
    kickSync();
    changed();
  });
  Uint8List? cachedPreview(ArchiveRecord record) =>
      _previewBytes[record.blob?['hash']];
  double? photoRatio(ArchiveRecord record) {
    final stored = record.data['photo_aspect'];
    if (stored is num && stored.isFinite && stored > 0) {
      return stored.toDouble();
    }
    return _photoAspects[record.blob?['hash']];
  }

  Future<void> _ensurePhotoLayouts() async {
    final index = File(p.join(archive.directory.path, 'photo-layouts.enc'));
    if (await index.exists()) {
      try {
        final saved = await archive.cipher.openJson(
          await index.readAsBytes(),
          'photo-layouts',
        );
        for (final entry in saved.entries) {
          if (validDigest(entry.key) &&
              entry.value is num &&
              (entry.value as num).isFinite &&
              (entry.value as num) > 0) {
            _photoAspects[entry.key] = (entry.value as num).toDouble();
          }
        }
      } catch (_) {}
    }
    var updated = false;
    for (final r in materials.where(
      (r) => r.kind == 'photo' && r.blob != null,
    )) {
      if (photoRatio(r) != null) continue;
      final hash = r.blob!['hash'] as String;
      try {
        final cached = File(
          p.join(archive.directory.path, 'previews', '$hash.enc'),
        );
        Uint8List? bytes;
        if (await cached.exists()) {
          bytes = await archive.cipher.open(
            await cached.readAsBytes(),
            'preview:$hash',
          );
        } else if ((r.blob!['chunks'] as int) > 0) {
          bytes = await archive.cipher.open(
            await archive.encryptedChunk(hash, 0),
            'blob:$hash:0',
          );
        }
        final aspect = bytes == null ? null : photoAspect(bytes);
        if (aspect != null) {
          _photoAspects[hash] = aspect;
          updated = true;
        }
      } catch (_) {}
    }
    if (updated) {
      await atomicWrite(
        index,
        await archive.cipher.sealJson(_photoAspects, 'photo-layouts'),
      );
    }
  }

  Uint8List _rememberPreview(String hash, Uint8List bytes) {
    if (_previewBytes.length >= 60) {
      _previewBytes.remove(_previewBytes.keys.first);
    }
    _previewBytes[hash] = bytes;
    return bytes;
  }

  Future<Uint8List?> preview(ArchiveRecord record) {
    final hash = record.blob?['hash'] as String?;
    if (hash == null || clearing) return Future.value(null);
    if (_previews.length > 60) _previews.remove(_previews.keys.first);
    final owner = archive, generation = _generation;
    return _previews.putIfAbsent(
      hash,
      () => _previewJobs.run(() async {
        if (generation != _generation || clearing) return null;
        File? clear;
        try {
          final cached = File(
            p.join(owner.directory.path, 'previews', '$hash.enc'),
          );
          if (await cached.exists()) {
            final bytes = await owner.cipher.open(
              await cached.readAsBytes(),
              'preview:$hash',
            );
            if (generation == _generation && !clearing) {
              _rememberPreview(hash, bytes);
            }
            return bytes;
          }
          if (!await owner.hasBlob(record.blob!)) return null;
          clear = await owner.materialize(
            record.blob!,
            temporary,
            record.data['filename'] as String? ?? record.title,
          );
          final bytes = await localThumbnail(
            clear,
            record.data['filename'] as String? ?? record.title,
          );
          if (bytes != null && generation == _generation && !clearing) {
            await atomicWrite(
              cached,
              await owner.cipher.seal(bytes, 'preview:$hash'),
            );
          }
          if (bytes != null && generation == _generation && !clearing) {
            _rememberPreview(hash, bytes);
          }
          return bytes;
        } catch (_) {
          return null;
        } finally {
          if (clear != null && await clear.exists()) await clear.delete();
        }
      }),
    );
  }

  Future<File> original(ArchiveRecord record) async {
    if (record.blob == null) {
      throw const FormatException('У этой записи нет файла.');
    }
    if (!await archive.hasBlob(record.blob!)) {
      await sync();
      if (!await archive.hasBlob(record.blob!)) {
        throw const FormatException(
          'Оригинал ожидает передачи со второго устройства.',
        );
      }
    }
    return archive.materialize(
      record.blob!,
      temporary,
      record.data['filename'] as String? ?? record.title,
    );
  }

  Future<File> backup() => ArchiveBackup.export(archive, temporary);
  Future<void> restoreBackup(File file) async {
    await ArchiveBackup.import(file, archive);
    await _ensurePhotoLayouts();
    kickSync();
    changed();
  }

  Future<void> changeMasterPassword(String password) =>
      _vaultWrites.run(() async {
        if (_vault == null || _vaultPassword == null) {
          throw const FormatException('Сначала открой хранилище.');
        }
        final record = vaultRecord, previous = _vaultPassword;
        if (record == null) {
          throw const FormatException('Сначала сохрани запись в хранилище.');
        }
        final envelope = await PasswordEnvelope.encrypt(_vault!, password);
        if (!vaultOpen || previous != _vaultPassword) {
          throw const FormatException('Хранилище заблокировано.');
        }
        await archive.save(
          {...record.data, 'envelope': envelope},
          recordId: record.id,
          expectedHeads: record.heads.map((r) => r.id).toList(),
        );
        _vaultPassword = password;
        touchVault();
        kickSync();
        changed();
      });
  void resume() {
    if (clearing) return;
    paused = false;
    _pump();
    kickSync();
  }

  void pause() {
    paused = true;
    lockVault();
  }

  static String friendly(Object e) {
    if (e is FormatException) return e.message;
    if (e is AiFailure || e is RemoteFailure) return e.toString();
    if (e is FileSystemException) {
      return 'Не удалось прочитать или сохранить файл. Проверь свободное место и доступ.';
    }
    if (e is TimeoutException) {
      return 'Обработка заняла слишком много времени. Оригинал сохранён.';
    }
    return 'Действие не завершилось. Данные сохранены; попробуй ещё раз.';
  }

  @override
  void dispose() {
    disposed = true;
    _periodic?.cancel();
    _syncTimer?.cancel();
    _vaultTimer?.cancel();
    ai.client.close();
    _relay?.remote.close();
    super.dispose();
  }
}
