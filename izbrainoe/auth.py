import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

def validate_telegram(data: str, token: str, now=None) -> int:
    """Telegram Mini Apps HMAC validation; never trust initDataUnsafe."""
    if not token or len(data) > 16384:
        raise ValueError('Нет данных авторизации')
    pairs = parse_qsl(data, keep_blank_values=True)
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise ValueError('Повторяющиеся поля')
    received = fields.pop('hash', '')
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise ValueError('Неверная подпись')
    age = (now or time.time()) - int(fields['auth_date'])
    if age < -60 or age > 86400:
        raise ValueError('Откройте мини-приложение заново')
    user = json.loads(fields['user'])
    uid = user['id']
    if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
        raise ValueError('Неверный пользователь')
    return uid
