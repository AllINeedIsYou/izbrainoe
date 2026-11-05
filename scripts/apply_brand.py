"""Export the approved galaxy source into platform assets; no generation or secrets."""
from pathlib import Path
import base64
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'assets/brand/galaxy-master.png'

def export_assets():
    source = Image.open(SOURCE).convert('RGB')
    def save(path, size, rounded=False):
        image = source.resize((size,size),Image.Resampling.LANCZOS)
        if rounded:
            image=image.convert('RGBA')
            mask=Image.new('L',(size,size));ImageDraw.Draw(mask).rounded_rectangle((0,0,size-1,size-1),radius=size*.22,fill=255);image.putalpha(mask)
        path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
    save(ROOT/'assets/logo.png',1024)
    save(ROOT/'assets/logo-telegram.jpg',1024)
    save(ROOT/'izbrainoe/static/logo.png',256)
    save(ROOT/'izbrainoe/static/favicon.png',64)
    source.resize((256,256),Image.Resampling.LANCZOS).save(ROOT/'izbrainoe/static/favicon.ico',sizes=[(16,16),(32,32),(48,48),(64,64)])
    save(ROOT/'apps/izbrainoe/assets/logo.png',512)
    res=ROOT/'apps/izbrainoe/android/app/src/main/res'
    for density,size in [('mdpi',48),('hdpi',72),('xhdpi',96),('xxhdpi',144),('xxxhdpi',192)]:
        save(res/f'mipmap-{density}/ic_launcher.png',size)
    save(res/'drawable-nodpi/brand_splash.png',512)
    foreground=Image.new('RGBA',(432,432))
    mark=source.resize((336,336),Image.Resampling.LANCZOS)
    # Alpha export preserves the approved white mark on the adaptive black base.
    alpha=ImageOps.grayscale(mark)
    white=Image.new('RGBA',mark.size,(255,255,255,255));white.putalpha(alpha)
    foreground.alpha_composite(white,(48,48))
    foreground.save(res/'drawable-nodpi/brand_foreground.png')
    for size in (16,32,64,128,256,512,1024):
        save(ROOT/f'apps/izbrainoe/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_{size}.png',size,rounded=True)
    encoded=base64.b64encode((ROOT/'izbrainoe/static/logo.png').read_bytes()).decode()
    (ROOT/'izbrainoe/static/icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><title>Изbrainое — галактика</title><image width="256" height="256" href="data:image/png;base64,'+encoded+'"/></svg>')
    print('Updated app, Android launcher, splash, Mac and Telegram assets.')

if __name__=='__main__': export_assets()
