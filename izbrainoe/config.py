import os
from pathlib import Path
from dataclasses import dataclass,field
import json
import secrets
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
    token: str = field(default='',repr=False)
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
    ai_provider: str = 'local'
    gemini_key: str = field(default='',repr=False)
    gemini_model: str = 'gemini-3.8-flash'
    ai_monthly_limit_usd: float = 5.0
    provod_key: str = field(default='',repr=False)
    provod_monthly_limit_rub: float = 1000.0

    def save_ai(self):
        path=self.data_dir/'ai-settings.json'
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temporary=path.with_name('.ai-settings-'+secrets.token_hex(8))
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w') as out:json.dump({'provider':self.ai_provider,'gemini_key':self.gemini_key,
                'gemini_model':self.gemini_model,'monthly_limit_usd':self.ai_monthly_limit_usd,
                'provod_key':self.provod_key,'provod_monthly_limit_rub':self.provod_monthly_limit_rub},out)
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls):
        read_env(ROOT / 'Настройки.env')
        read_env()
        data_dir=Path(os.getenv('DATA_DIR',str(ROOT/'data')))
        path=data_dir/'ai-settings.json'
        ai=json.loads(path.read_text()) if path.exists() else {}
        url = os.getenv('OLLAMA_URL', '')
        # Personal data may only be passed to a loopback model in this version.
        if url and urlparse(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('OLLAMA_URL должен указывать на локальный компьютер.')
        return cls(data_dir=data_dir,
                   token=os.getenv('BOT_TOKEN', ''), bot_username=os.getenv('BOT_USERNAME', 'izbrainoe_bot'),
                   public_url=os.getenv('PUBLIC_URL', '').rstrip('/'),
                   allowed_ids=tuple(int(i.strip()) for i in os.getenv('ALLOWED_USER_IDS', '').split(',') if i.strip()),
                   ollama_url=url, embed_model=os.getenv('EMBED_MODEL', ''),
                   chat_model=os.getenv('CHAT_MODEL', ''), port=int(os.getenv('PORT', '8765')),
                   enable_tunnel=os.getenv('ENABLE_TUNNEL','1')=='1',
                   pdf_pages=max(2,min(4,int(os.getenv('PDF_PREVIEW_PAGES','4')))),
                   web_lookup=os.getenv('WEB_LOOKUP','0')=='1',web_backend=os.getenv('WEB_BACKEND','bing'),
                   max_upload_mb=max(20,min(100,int(os.getenv('MAX_UPLOAD_MB','100')))),
                   ai_provider=ai.get('provider',os.getenv('AI_PROVIDER','local')),
                   gemini_key=ai.get('gemini_key',os.getenv('GEMINI_API_KEY','')),
                   gemini_model=ai.get('gemini_model','gemini-3.8-flash'),
                   ai_monthly_limit_usd=float(ai.get('monthly_limit_usd',os.getenv('AI_MONTHLY_LIMIT_USD','5'))),
                   provod_key=ai.get('provod_key',''),
                   provod_monthly_limit_rub=float(ai.get('provod_monthly_limit_rub',1000)))
