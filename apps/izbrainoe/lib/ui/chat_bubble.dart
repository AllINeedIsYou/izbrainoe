import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../core/archive_store.dart';
import '../core/controller.dart';
import 'detail.dart';
import 'material_preview.dart';
import 'widgets.dart';

class ChatBubble extends StatefulWidget {
  final BrainController brain;
  final ArchiveRecord record;
  final VoidCallback onTap;
  const ChatBubble({
    super.key,
    required this.brain,
    required this.record,
    required this.onTap,
  });
  @override
  State<ChatBubble> createState() => _ChatBubbleState();
}

class _ChatBubbleState extends State<ChatBubble> {
  double dx = 0;
  bool deleting = false;
  late Future<Uint8List?> _preview;
  late double _aspect;
  @override
  void initState() {
    super.initState();
    _loadMedia();
  }

  void _loadMedia() {
    _aspect = widget.brain.photoRatio(record) ?? 1;
    _preview = widget.brain.preview(record);
  }

  @override
  void didUpdateWidget(covariant ChatBubble old) {
    super.didUpdateWidget(old);
    if (old.record.blob?['hash'] != record.blob?['hash'] ||
        old.brain != widget.brain) {
      _loadMedia();
    }
  }

  ArchiveRecord get record => widget.record;
  Future<void> remove() async {
    if (deleting) return;
    deleting = true;
    final confirmed = await confirmDelete(
      context,
      'Удалить сообщение?',
      'Материал будет перемещён в корзину.',
    );
    if (confirmed && mounted) {
      await safely(context, () => widget.brain.trashRecord(record));
    }
    deleting = false;
  }

  @override
  Widget build(BuildContext context) {
    final maxWidth = min(420.0, MediaQuery.sizeOf(context).width * .85);
    return Align(
      alignment: Alignment.centerRight,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 7),
        child: GestureDetector(
          onHorizontalDragStart: (_) => setState(() => dx = 0),
          onHorizontalDragUpdate: (d) =>
              setState(() => dx = (dx + d.delta.dx).clamp(-90, 0)),
          onHorizontalDragCancel: () => setState(() => dx = 0),
          onHorizontalDragEnd: (d) {
            final removeNow =
                dx < -48 || (dx < -18 && (d.primaryVelocity ?? 0) < -650);
            setState(() => dx = 0);
            if (removeNow) remove();
          },
          onLongPress: () => showRecordActions(context, widget.brain, record),
          child: Stack(
            alignment: Alignment.centerRight,
            children: [
              if (dx < -12)
                const Padding(
                  padding: EdgeInsets.only(right: 14),
                  child: Icon(Icons.delete_outline, color: accent),
                ),
              Transform.translate(
                offset: Offset(dx, 0),
                child: ConstrainedBox(
                  constraints: BoxConstraints(maxWidth: maxWidth),
                  child: record.kind == 'photo'
                      ? _photo(context, maxWidth)
                      : _message(context),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _meta({bool overlay = false}) => Container(
    padding: overlay
        ? const EdgeInsets.symmetric(horizontal: 7, vertical: 3)
        : EdgeInsets.zero,
    decoration: overlay
        ? BoxDecoration(
            color: Colors.black.withValues(alpha: .68),
            borderRadius: BorderRadius.circular(10),
          )
        : null,
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (record.conflict)
          const Padding(
            padding: EdgeInsets.only(right: 4),
            child: Icon(Icons.call_split, size: 12, color: accent),
          ),
        Text(
          timeLabel(record.data['created'] as int? ?? record.current.created),
          style: const TextStyle(fontSize: 10, color: muted),
        ),
        const SizedBox(width: 4),
        Icon(
          record.data['analysis_state'] == 'pending' && widget.brain.ai.enabled
              ? Icons.schedule
              : Icons.done_all,
          size: 13,
          color: accent,
        ),
      ],
    ),
  );
  Widget _message(BuildContext context) => Material(
    color: surface,
    borderRadius: const BorderRadius.only(
      topLeft: Radius.circular(17),
      topRight: Radius.circular(17),
      bottomLeft: Radius.circular(17),
      bottomRight: Radius.circular(5),
    ),
    child: InkWell(
      onTap: widget.onTap,
      borderRadius: BorderRadius.circular(17),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(11, 9, 9, 6),
        child: IntrinsicWidth(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (record.blob != null)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SizedBox(
                      width: 48,
                      height: 55,
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(7),
                        child: RecordPreview(
                          brain: widget.brain,
                          record: record,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Flexible(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            record.title,
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(height: 5),
                          Text(
                            sizeLabel(record.blob!['size'] as int),
                            style: const TextStyle(fontSize: 11, color: muted),
                          ),
                        ],
                      ),
                    ),
                  ],
                )
              else
                Text(
                  record.data['body'] as String? ?? record.title,
                  style: const TextStyle(fontSize: 15, height: 1.35),
                ),
              if ((record.data['context'] as String? ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 7),
                  child: Text(
                    record.data['context'] as String,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12, color: muted),
                  ),
                ),
              const SizedBox(height: 3),
              Align(alignment: Alignment.centerRight, child: _meta()),
            ],
          ),
        ),
      ),
    ),
  );
  Widget _photo(BuildContext context, double maxWidth) {
    final maxHeight = min(500.0, MediaQuery.sizeOf(context).height * .58);
    final width = min(maxWidth, maxHeight * _aspect), height = width / _aspect;
    final caption = record.data['context'] as String? ?? '';
    // Geometry is decided before loading. A thumbnail never resizes the row.
    return SizedBox(
      width: width,
      child: Material(
        color: surface,
        clipBehavior: Clip.antiAlias,
        borderRadius: const BorderRadius.only(
          topLeft: Radius.circular(16),
          topRight: Radius.circular(16),
          bottomLeft: Radius.circular(16),
          bottomRight: Radius.circular(5),
        ),
        child: InkWell(
          onTap: widget.onTap,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: width,
                height: height,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    FutureBuilder<Uint8List?>(
                      future: _preview,
                      initialData: widget.brain.cachedPreview(record),
                      builder: (context, snapshot) {
                        if (snapshot.data == null) {
                          return const Center(
                            child: Icon(Icons.image_outlined, size: 32),
                          );
                        }
                        return Image.memory(
                          snapshot.data!,
                          fit: BoxFit.contain,
                          gaplessPlayback: true,
                        );
                      },
                    ),
                    if (caption.isEmpty)
                      Positioned(
                        right: 6,
                        bottom: 6,
                        child: _meta(overlay: true),
                      ),
                  ],
                ),
              ),
              if (caption.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(10, 8, 8, 5),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(caption, style: const TextStyle(fontSize: 14)),
                      Align(alignment: Alignment.centerRight, child: _meta()),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
