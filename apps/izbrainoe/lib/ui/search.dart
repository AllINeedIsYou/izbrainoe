import 'package:flutter/material.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import 'detail.dart';
import 'vault.dart';
import 'widgets.dart';

class SearchPage extends StatefulWidget {
  final BrainController brain;
  final String initialQuery;
  const SearchPage({super.key, required this.brain, this.initialQuery = ''});
  @override
  State<SearchPage> createState() => _SearchState();
}

class _SearchState extends State<SearchPage> {
  late final TextEditingController query = TextEditingController(
    text: widget.initialQuery,
  );
  List<ArchiveRecord>? results;
  String? error;
  bool busy = false;
  int generation = 0;
  @override
  void initState() {
    super.initState();
    if (widget.initialQuery.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _search());
    }
  }

  @override
  void dispose() {
    generation++;
    query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final q = query.text.trim();
    if (q.isEmpty) return;
    if (widget.brain.credentialQuery(q)) {
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
          builder: (_) => Scaffold(
            appBar: AppBar(title: const Text('Пароли')),
            body: VaultPage(brain: widget.brain, initialQuery: q),
          ),
        ),
      );
      return;
    }
    final request = ++generation;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final found = await widget.brain.search(q);
      if (mounted && generation == request) setState(() => results = found);
    } catch (e) {
      if (mounted && generation == request) {
        setState(() => error = BrainController.friendly(e));
      }
    } finally {
      if (mounted && generation == request) setState(() => busy = false);
    }
  }

  Widget _content() {
    if (error != null) {
      return EmptyState(
        icon: Icons.wifi_off,
        title: 'Поиск не завершён',
        message: error!,
        action: OutlinedButton(
          onPressed: _search,
          child: const Text('Повторить'),
        ),
      );
    }
    if (results == null) {
      return const EmptyState(
        icon: Icons.search,
        title: 'Вспомни, что нужно',
        message: 'Название, тема или описание сохранённого материала.',
      );
    }
    if (results!.isEmpty) {
      return const EmptyState(
        icon: Icons.search_off,
        title: 'Ничего не нашлось',
        message: 'Попробуй другую формулировку.',
      );
    }
    return ListView(
      padding: const EdgeInsets.all(18),
      children: [
        Text(
          'Нашлось: ${results!.length}',
          style: const TextStyle(color: muted, fontSize: 13),
        ),
        const SizedBox(height: 16),
        ...results!.map(
          (r) => MaterialTile(
            record: r,
            brain: widget.brain,
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => DetailPage(brain: widget.brain, id: r.id),
              ),
            ),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Найти в архиве')),
    body: SafeArea(
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 12, 18, 18),
            child: TextField(
              controller: query,
              autofocus: widget.initialQuery.isEmpty,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _search(),
              decoration: InputDecoration(
                hintText: 'Что найти?',
                prefixIcon: const Icon(Icons.search, color: accent),
                suffixIcon: IconButton(
                  tooltip: 'Найти',
                  onPressed: busy ? null : _search,
                  icon: const Icon(Icons.arrow_forward),
                ),
              ),
            ),
          ),
          if (busy) const LinearProgressIndicator(minHeight: 2),
          Expanded(child: _content()),
        ],
      ),
    ),
  );
}
