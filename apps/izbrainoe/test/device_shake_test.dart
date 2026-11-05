import 'package:flutter_test/flutter_test.dart';
import 'package:izbrainoe/core/device_shake.dart';

void main() {
  test(
    'shake needs opposite peaks and ignores ordinary movement and one knock',
    () {
      final detector = ShakeDetector();
      for (var t = 0; t <= 1000; t += 20) {
        expect(
          detector.sample(t, (t ~/ 20).isEven ? 1 : -1, .5, 9.81),
          isFalse,
        );
      }
      expect(detector.sample(1020, 24, 0, 9.81), isFalse);
      for (var t = 1040; t <= 2020; t += 20) {
        expect(detector.sample(t, 0, 0, 9.81), isFalse);
      }
      expect(detector.sample(2040, 24, 0, 9.81), isFalse);
      expect(detector.sample(2160, -24, 0, 9.81), isTrue);
      expect(detector.sample(2280, 24, 0, 9.81), isFalse);
      expect(detector.sample(2400, -24, 0, 9.81), isFalse);
      for (var t = 2420; t <= 3820; t += 20) {
        expect(detector.sample(t, 0, 0, 9.81), isFalse);
      }
      expect(detector.sample(3840, 24, 0, 9.81), isFalse);
      expect(detector.sample(3960, -24, 0, 9.81), isTrue);
    },
  );
}
