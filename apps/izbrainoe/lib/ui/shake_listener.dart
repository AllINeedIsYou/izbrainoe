import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/device_shake.dart';

/// Only the visible route acts; dialogs and background routes are left alone.
class ShakeListener extends StatefulWidget {
  final Widget child;
  final VoidCallback onShake;
  final Stream<void>? events;
  const ShakeListener({
    super.key,
    required this.child,
    required this.onShake,
    this.events,
  });
  @override
  State<ShakeListener> createState() => _ShakeListenerState();
}

class _ShakeListenerState extends State<ShakeListener> {
  StreamSubscription<void>? subscription;
  @override
  void initState() {
    super.initState();
    subscription = (widget.events ?? DeviceShakes.events).listen((_) {
      final lifecycle = WidgetsBinding.instance.lifecycleState;
      if (!mounted ||
          (lifecycle != null && lifecycle != AppLifecycleState.resumed)) {
        return;
      }
      if (ModalRoute.of(context)?.isCurrent != true) return;
      FocusManager.instance.primaryFocus?.unfocus();
      unawaited(HapticFeedback.lightImpact().catchError((_) {}));
      widget.onShake();
    }, onError: (Object _) {});
  }

  @override
  void dispose() {
    unawaited(subscription?.cancel());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
