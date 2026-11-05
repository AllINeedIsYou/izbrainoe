import 'dart:typed_data';

import 'package:image/image.dart' as img;

/// Read only dimensions, never decode image pixels. Accepts a bounded prefix.
double? photoAspect(Uint8List bytes) {
  try {
    final b = ByteData.sublistView(bytes);
    double? ratio(int w, int h, [int orientation = 1]) {
      if (w < 1 || h < 1 || w > 100000 || h > 100000) return null;
      return orientation >= 5 && orientation <= 8 ? h / w : w / h;
    }

    if (bytes.length >= 24 && b.getUint32(0) == 0x89504e47) {
      return ratio(b.getUint32(16), b.getUint32(20));
    }
    if (bytes.length >= 10 &&
        bytes[0] == 71 &&
        bytes[1] == 73 &&
        bytes[2] == 70) {
      return ratio(
        b.getUint16(6, Endian.little),
        b.getUint16(8, Endian.little),
      );
    }
    if (bytes.length >= 4 && bytes[0] == 255 && bytes[1] == 216) {
      var offset = 2, orientation = 1, width = 0, height = 0;
      while (offset + 4 < bytes.length) {
        if (bytes[offset] != 255) break;
        while (offset < bytes.length && bytes[offset] == 255) {
          offset++;
        }
        final marker = bytes[offset++];
        if (marker == 0xda || marker == 0xd9) break;
        if (marker == 0x01 || marker >= 0xd0 && marker <= 0xd7) continue;
        final length = b.getUint16(offset);
        if (length < 2 || offset + length > bytes.length) break;
        if (marker == 0xe1 &&
            length >= 16 &&
            b.getUint32(offset + 2) == 0x45786966) {
          final tiff = offset + 8;
          final endian = b.getUint16(tiff) == 0x4949
              ? Endian.little
              : Endian.big;
          final directory = tiff + b.getUint32(tiff + 4, endian);
          if (directory >= tiff && directory + 2 < offset + length) {
            final entries = b.getUint16(directory, endian);
            for (var i = 0; i < entries && i < 256; i++) {
              final entry = directory + 2 + i * 12;
              if (entry + 12 > offset + length) break;
              if (b.getUint16(entry, endian) == 0x0112 &&
                  b.getUint16(entry + 2, endian) == 3) {
                orientation = b.getUint16(entry + 8, endian);
                break;
              }
            }
          }
        }
        if ([
              0xc0,
              0xc1,
              0xc2,
              0xc3,
              0xc5,
              0xc6,
              0xc7,
              0xc9,
              0xca,
              0xcb,
              0xcd,
              0xce,
              0xcf,
            ].contains(marker) &&
            length >= 7) {
          height = b.getUint16(offset + 3);
          width = b.getUint16(offset + 5);
        }
        offset += length;
      }
      return ratio(width, height, orientation);
    }
    final info = img.findDecoderForData(bytes)?.startDecode(bytes);
    if (info != null) return ratio(info.width, info.height);
  } catch (_) {
    // Damaged or unsupported media still gets a fixed, non-jumping placeholder.
  }
  return null;
}
