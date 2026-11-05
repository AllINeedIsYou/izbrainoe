import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'widgets.dart';

/// Static, non-interactive wallpaper: the approved “За краем”, intensity 23%.
class EventHorizonBackground extends StatelessWidget {
  final Widget child;
  const EventHorizonBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final referenceHeight = math.max(
      240.0,
      media.size.height - media.padding.vertical - kToolbarHeight,
    );
    return Stack(
      fit: StackFit.expand,
      children: [
        Positioned.fill(
          child: IgnorePointer(
            child: RepaintBoundary(
              child: CustomPaint(painter: _HorizonPainter(referenceHeight)),
            ),
          ),
        ),
        child,
      ],
    );
  }
}

class _HorizonPainter extends CustomPainter {
  final double referenceHeight;
  const _HorizonPainter(this.referenceHeight);

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(background, BlendMode.src);
    final center = Offset(-size.width * .6, referenceHeight * .57);
    final radius = size.width * .96;
    final glow = Rect.fromCircle(center: center, radius: radius * 1.65);
    canvas.drawRect(
      glow,
      Paint()
        ..shader = RadialGradient(
          colors: [
            Colors.white.withValues(alpha: 0),
            Colors.white.withValues(alpha: .23 * .18),
            Colors.white.withValues(alpha: 0),
          ],
          stops: const [.85 / 1.65, (.85 + .8 * .28) / 1.65, 1],
        ).createShader(glow),
    );
    canvas.drawCircle(
      center,
      radius * .994,
      Paint()..color = const Color(0xff0b0b0b),
    );
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -1.25,
      2.55,
      false,
      Paint()
        ..color = Colors.white.withValues(alpha: .23 * .64)
        ..style = PaintingStyle.stroke
        ..strokeWidth = .8
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4.5),
    );
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -1.25,
      2.55,
      false,
      Paint()
        ..color = Colors.white.withValues(alpha: .23 * .64)
        ..style = PaintingStyle.stroke
        ..strokeWidth = .8,
    );
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius + 4),
      -.72,
      1.52,
      false,
      Paint()
        ..color = Colors.white.withValues(alpha: .23 * .25)
        ..style = PaintingStyle.stroke
        ..strokeWidth = .6,
    );
    var seed = 97133;
    double random() {
      seed = (seed * 1664525 + 1013904223) & 0xffffffff;
      return seed / 4294967296;
    }

    for (var i = 0; i < 12; i++) {
      final point = Offset(random() * size.width, random() * referenceHeight);
      final r = .35 + random() * .5;
      canvas.drawCircle(
        point,
        r,
        Paint()
          ..color = Colors.white.withValues(alpha: .23 * (.25 + random() * .5)),
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_HorizonPainter oldDelegate) =>
      referenceHeight != oldDelegate.referenceHeight;
}
