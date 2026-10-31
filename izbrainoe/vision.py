"""Portable image/PDF previews; recognition is performed by Gemini, not macOS."""
import base64
import io
import threading
from pathlib import Path
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
register_heif_opener()

PDF_LOCK=threading.Lock()  # PDFium is not thread-safe, even across different documents.
Image.MAX_IMAGE_PIXELS=40_000_000


def pack(image,number,max_edge):
    image.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
    if image.mode!='RGB':
        bg=Image.new('RGB',image.size,'white')
        if 'A' in image.getbands():bg.paste(image,mask=image.getchannel('A'))
        else:bg.paste(image.convert('RGB'))
        image=bg
    buffer=io.BytesIO();image.save(buffer,'JPEG',quality=85,optimize=True)
    return {'number':number,'data':base64.b64encode(buffer.getvalue()).decode(),'mime_type':'image/jpeg','width':image.width,'height':image.height}


def previews(path,photo,pages,max_edge):
    if photo:
        with Image.open(path) as source:
            if source.width*source.height>Image.MAX_IMAGE_PIXELS:raise ValueError('image too large')
            image=ImageOps.exif_transpose(source)
            return [pack(image,1,max_edge)]
    import pypdfium2 as pdfium
    with PDF_LOCK:
        doc=pdfium.PdfDocument(str(path))
        try:
            output=[]
            for n in range(min(pages,len(doc))):
                page=doc[n]
                try:
                    w,h=page.get_size()
                    bitmap=page.render(scale=min(3,max_edge/max(w,h)),rev_byteorder=True)
                    try:output.append(pack(bitmap.to_pil(),n+1,max_edge))
                    finally:bitmap.close()
                finally:page.close()
            return output
        finally:doc.close()
