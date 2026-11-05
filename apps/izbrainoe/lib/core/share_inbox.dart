import 'dart:async';
import 'dart:collection';

import 'package:receive_sharing_intent/receive_sharing_intent.dart';

/// Subscribe before loading the encrypted archive. The Android plugin does not
/// replay warm intents delivered before its event-channel listener is attached.
class ShareInbox {
  final ReceiveSharingIntent source;
  final Queue<List<SharedMediaFile>> _pending = Queue();
  final List<List<SharedMediaFile>> _duringStart = [];
  StreamSubscription<List<SharedMediaFile>>? _subscription;
  Future<void> Function(List<SharedMediaFile>)? _receiver;
  bool _starting = true, _draining = false;
  ShareInbox({ReceiveSharingIntent? source})
    : source = source ?? ReceiveSharingIntent.instance;
  Future<void> start() async {
    _subscription = source.getMediaStream().listen((batch) {
      if (batch.isEmpty) return;
      if (_starting) {
        _duringStart.add(batch);
      } else {
        _enqueue(batch);
      }
    }, onError: (Object _) {});
    try {
      final initial = await source.getInitialMedia();
      if (initial.isNotEmpty) _enqueue(initial);
    } catch (_) {
    } finally {
      for (final batch in _duringStart) {
        _enqueue(batch);
      }
      _duringStart.clear();
      _starting = false;
    }
  }

  void _enqueue(List<SharedMediaFile> batch) {
    _pending.add(batch);
    unawaited(_drain());
  }

  void attach(Future<void> Function(List<SharedMediaFile>) receiver) {
    _receiver = receiver;
    unawaited(_drain());
  }

  void detach() {
    _receiver = null;
  }

  Future<void> _drain() async {
    if (_draining) return;
    _draining = true;
    try {
      while (_receiver != null && _pending.isNotEmpty) {
        final batch = _pending.removeFirst();
        try {
          await _receiver!(batch);
        } catch (_) {}
      }
    } finally {
      _draining = false;
    }
  }

  Future<void> dispose() async {
    detach();
    await _subscription?.cancel();
  }
}
