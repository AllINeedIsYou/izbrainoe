import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/services.dart';

/// Requires two opposite acceleration peaks; a single knock is not a shake.
class ShakeDetector {
  List<double>? _gravity, _peak;
  int? _lastSample, _peakAt;
  int _blockedUntil = 0;
  bool _aboveThreshold = false;

  bool sample(int milliseconds, double x, double y, double z) {
    if (![x, y, z].every((v) => v.isFinite)) return false;
    final values = [x, y, z];
    if (_gravity == null ||
        _lastSample == null ||
        milliseconds <= _lastSample! ||
        milliseconds - _lastSample! > 800) {
      _gravity = values;
      _lastSample = milliseconds;
      _peak = null;
      _peakAt = null;
      _aboveThreshold = false;
      return false;
    }
    final dt = milliseconds - _lastSample!;
    _lastSample = milliseconds;
    final alpha = math.exp(-dt / 180);
    for (var i = 0; i < 3; i++) {
      _gravity![i] = alpha * _gravity![i] + (1 - alpha) * values[i];
    }
    final linear = List.generate(3, (i) => values[i] - _gravity![i]);
    final force = math.sqrt(linear.fold(0.0, (sum, v) => sum + v * v));
    if (force < 6) _aboveThreshold = false;
    if (milliseconds < _blockedUntil || force < 12) return false;
    if (_aboveThreshold) {
      // Opposite peaks can occur without an intervening low-force sample.
      if (_peak == null || _dot(linear, _peak!) >= 0) return false;
    }
    _aboveThreshold = true;
    if (_peak != null && _peakAt != null) {
      final elapsed = milliseconds - _peakAt!;
      final previousForce = math.sqrt(_dot(_peak!, _peak!));
      final cosine = _dot(linear, _peak!) / (force * previousForce);
      if (elapsed >= 75 && elapsed <= 650 && cosine < -.35) {
        _blockedUntil = milliseconds + 1500;
        _peak = null;
        _peakAt = null;
        return true;
      }
      if (elapsed < 75) return false;
    }
    _peak = linear;
    _peakAt = milliseconds;
    return false;
  }

  double _dot(List<double> a, List<double> b) =>
      a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

class DeviceShakes {
  static final Stream<void> events = _create();
  static Stream<void> _create() {
    if (!Platform.isAndroid) return const Stream<void>.empty();
    final detector = ShakeDetector();
    return const EventChannel('app.izbrainoe/motion')
        .receiveBroadcastStream()
        .where((event) {
          if (event is! List || event.length != 4) return false;
          final values = event.cast<num>();
          return detector.sample(
            values[0].toInt(),
            values[1].toDouble(),
            values[2].toDouble(),
            values[3].toDouble(),
          );
        })
        .map<void>((_) {})
        .asBroadcastStream();
  }
}
