import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';
import 'package:izbrainoe/core/share_inbox.dart';

void main() {
  test(
    'incoming shares during archive initialization are buffered and serialized',
    () async {
      final stream = StreamController<List<SharedMediaFile>>(sync: true);
      final initial = SharedMediaFile(
        path: 'initial',
        type: SharedMediaType.text,
      );
      ReceiveSharingIntent.setMockValues(
        initialMedia: [initial],
        mediaStream: stream.stream,
      );
      final inbox = ShareInbox();
      final boot = inbox.start();
      stream.add([
        SharedMediaFile(path: 'during-boot', type: SharedMediaType.text),
      ]);
      await boot;
      final received = <String>[];
      final complete = Completer<void>();
      var active = 0, maxActive = 0;
      inbox.attach((batch) async {
        active++;
        if (active > maxActive) maxActive = active;
        await Future<void>.delayed(const Duration(milliseconds: 2));
        received.add(batch.single.path);
        active--;
        if (received.length == 3) complete.complete();
      });
      stream.add([
        SharedMediaFile(path: 'after-boot', type: SharedMediaType.text),
      ]);
      await complete.future;
      expect(received, ['initial', 'during-boot', 'after-boot']);
      expect(maxActive, 1);
      await inbox.dispose();
      await stream.close();
    },
  );
}
