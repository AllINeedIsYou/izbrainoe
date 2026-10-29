import os
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

def read_env(path=ROOT / '.env'):
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

@dataclass
class Config:
    data_dir: Path = ROOT / 'data'
    token: str = ''
    bot_username: str = 'izbrainoe_bot'
    public_url: str = ''
    allowed_ids: tuple = ()
    ollama_url: str = ''
    embed_model: str = ''
    chat_model: str = ''
    port: int = 8765
    enable_tunnel: bool = True
    pdf_pages: int = 4
    web_lookup: bool = False
    web_backend: str = 'bing'
    max_upload_mb: int = 100

    @classmethod
    def load(cls):
        read_env(ROOT / 'Настройки.env')
        read_env()
        url = os.getenv('OLLAMA_URL', '')
        # Personal data may only be passed to a loopback model in this version.
        if url and urlparse(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('OLLAMA_URL должен указывать на локальный компьютер.')
        return cls(data_dir=Path(os.getenv('DATA_DIR', str(ROOT / 'data'))),
                   token=os.getenv('BOT_TOKEN', ''), bot_username=os.getenv('BOT_USERNAME', 'izbrainoe_bot'),
                   public_url=os.getenv('PUBLIC_URL', '').rstrip('/'),
                   allowed_ids=tuple(int(i.strip()) for i in os.getenv('ALLOWED_USER_IDS', '').split(',') if i.strip()),
                   ollama_url=url, embed_model=os.getenv('EMBED_MODEL', ''),
                   chat_model=os.getenv('CHAT_MODEL', ''), port=int(os.getenv('PORT', '8765')),
                   enable_tunnel=os.getenv('ENABLE_TUNNEL','1')=='1',
                   pdf_pages=max(2,min(4,int(os.getenv('PDF_PREVIEW_PAGES','4')))),
                   web_lookup=os.getenv('WEB_LOOKUP','0')=='1',web_backend=os.getenv('WEB_BACKEND','bing'),
                   max_upload_mb=max(20,min(100,int(os.getenv('MAX_UPLOAD_MB','100')))))
