# Изbrainое — галактика

Утверждённый основной знак: белая наклонённая спиральная галактика на чёрном фоне. Рукава вытянуты, их концы заострены; светлое ядро и наклон отличают знак от цветка.

![Основной знак](galaxy-master.png)

## Источник и экспорт

`galaxy-master.png` — утверждённое изображение без последующей перерисовки. История концепта: `assets/logo-space-concepts/space-06.png` → `space-06-v2.png`. Исходный запрос imagegen сохранён рядом с вариантом.

Размеры экспортируются скриптом из корня проекта:

```sh
.venv/bin/python scripts/apply_brand.py
```

Скрипт требует Pillow. Он меняет размер и формат, подготавливает прозрачную маску адаптивного Android-значка и округление Mac-иконок. Содержание утверждённого знака не генерируется заново.

| Файл | Назначение |
| --- | --- |
| `assets/logo.png` | Общее изображение и README |
| `assets/logo-telegram.jpg` | Аватар Telegram-бота |
| `izbrainoe/static/logo.png` | Веб-интерфейс |
| `izbrainoe/static/favicon.png`, `favicon.ico`, `icon.svg` | Значок сайта |
| `apps/izbrainoe/assets/logo.png` | Интерфейс Android и будущей Mac-версии |
| `apps/izbrainoe/android/app/src/main/res/` | Launcher, themed icon, заставка Android |
| `apps/izbrainoe/macos/Runner/Assets.xcassets/AppIcon.appiconset/` | Значки macOS |

Существующий Mac-launcher на рабочем столе пересобирается командой `.venv/bin/python scripts/create_desktop_app.py`. Его значок также берётся из этого набора. Новый Flutter-клиент Mac остаётся отдельной незавершённой частью проекта.
