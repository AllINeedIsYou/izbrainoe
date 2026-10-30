"""Build a native macOS launcher; no keys or private data enter the app bundle."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path.home() / 'Desktop' / 'Изbrainое.app')
    args = parser.parse_args()
    target = args.output.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        info = plistlib.loads((target / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'local.izbrainoe.launcher':
            raise SystemExit('По этому пути уже находится другое приложение.')
    with tempfile.TemporaryDirectory(prefix='.izbrainoe-build-', dir=target.parent) as temp:
        bundle = Path(temp) / target.name
        contents = bundle / 'Contents'
        executable = contents / 'MacOS' / 'Izbrainoe'
        resources = contents / 'Resources'
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        subprocess.run(['xcrun', 'swiftc', str(ROOT / 'scripts/desktop.swift'), '-o', str(executable), '-framework', 'Cocoa'], check=True)
        (resources / 'project-path.txt').write_text(str(ROOT))
        iconset = Path(temp) / 'AppIcon.iconset'
        iconset.mkdir()
        logo = ROOT / 'izbrainoe/static/logo.png'
        for size in (16, 32, 128, 256):
            subprocess.run(['sips', '-z', str(size), str(size), str(logo), '--out', str(iconset / f'icon_{size}x{size}.png')], check=True, stdout=subprocess.DEVNULL)
        for size in (16, 128):
            shutil.copyfile(iconset / f'icon_{size*2}x{size*2}.png', iconset / f'icon_{size}x{size}@2x.png')
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'AppIcon.icns')], check=True)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.izbrainoe.launcher', 'CFBundleName': 'Изbrainое',
            'CFBundleDisplayName': 'Изbrainое', 'CFBundleExecutable': 'Izbrainoe',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
            'CFBundleIconFile': 'AppIcon', 'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '12.0',
        }))
        subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True, capture_output=True)
        if target.exists():
            shutil.rmtree(target)
        bundle.rename(target)
    print(f'Готово: {target}')


if __name__ == '__main__':
    main()
