# Конструктор форм (Flask + SQLite)

Локальное веб-приложение для работы с шаблонами форм (DOCX/ODT):
загрузка шаблонов, регистрация полей-плейсхолдеров `{{key}}`, справочники,
заполнение, сохранение в БД, печать и скачивание заполненного DOCX.

## Архитектура

- **app.py** — Flask-приложение: маршруты, загрузка шаблонов (mammoth → HTML),
  подстановка HTML-виджетов вместо `{{key}}`, сохранение форм в SQLite (JSON).
- **database.py** — чистый `sqlite3` (без ORM), схема БД, CRUD-функции,
  стартовые плейсхолдеры при первом запуске.
- **printer.py** — заполнение исходного DOCX через python-docx
  (замена на уровне параграфа: склейка runs → замена → запись в первый run).
- **uploads/** — загруженные шаблоны: `<uuid>.docx`, `<uuid>.html`.
- **templates/** — Jinja2-шаблоны страниц; **static/style.css** — стиль «под Windows».
- **tests/** — pytest + Flask test_client.

Стек: Python 3.10+, Flask, sqlite3, Jinja2, mammoth, python-docx,
чистые HTML/CSS/vanilla JS. Без Docker/gunicorn/nginx/Postgres/Redis.

## Установка на Kali Linux

```bash
sudo apt update
sudo apt install python3-venv libreoffice-writer firefox-esr
cd form_web
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> **Важно:** DOCX работает без LibreOffice (mammoth и python-docx — чистый Python); ODT требует установленный LibreOffice (`libreoffice --headless --convert-to docx`).

## Запуск

```bash
source venv/bin/activate
python app.py
```

Открыть http://127.0.0.1:5000 в firefox-esr. Сервер слушает только 127.0.0.1:5000.

## Создание примера шаблона

```bash
python scripts/make_sample_docx.py   # создаст sample_template.docx
```

Затем на странице **Шаблоны** выберите этот файл и нажмите «Загрузить».
Приложение сконвертирует его в HTML (mammoth), найдёт все `{{...}}`,
активирует шаблон и сообщит о неизвестных полях (например, `phone`) —
настройте их на странице «Поля формы».

## Основные страницы

| URL | Назначение |
|---|---|
| `/` | Активный шаблон с полями ввода; кнопки Сохранить / Печать / Скачать DOCX / Очистить |
| `/load`, `/form/<id>` | Сохранённые формы (JSON в data_json), открытие, удаление |
| `/templates` | Загрузка .docx/.odt, активация, скачивание исходника, удаление |
| `/placeholders` | Реестр полей: key, label, type, source, required, bold, порядок |
| `/lists` | Справочники persons, addresses + пользовательские списки |

Стартовые поля (создаются автоматически при первом запуске,
не перезаписываются): `person` (select/persons), `date1` (date),
`address` (select/addresses), `date2` (date) — все жирным (bold).

Источники значений select: `persons`, `addresses`,
пользовательские списки (`list:<id>` из таблицы custom_lists).

## Печать

- **HTML-печать**: кнопка «Печать» вызывает `window.print()`;
  `@media print` в `static/style.css` скрывает шапку/навигацию/кнопки,
  шрифт 12pt, поля 15 мм, формат A4.
- **Скачать DOCX**: `/download_docx` заполняет исходный шаблон python-docx
  и отдаёт файл `Форма_<display_name>.docx`. При ошибке — flash с предложением
  использовать HTML-печать.

## Тестирование

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

Тесты используют изолированную БД и uploads во временной директории
(файстура `app_env` в `tests/conftest.py`). Для загрузки шаблонов в тестах
используется DOCX, сгенерированный python-docx; LibreOffice в тестах не нужен.

## Структура проекта

```
form_web/
├── app.py
├── database.py
├── printer.py
├── requirements.txt
├── README.md
├── uploads/            # .gitkeep; файлы <uuid>.docx / <uuid>.html
├── templates/          # base, form, load, template_list, placeholder_*, lists, ...
├── static/style.css
├── scripts/make_sample_docx.py
└── tests/test_database.py, test_routes.py
```

## Безопасность и ограничения

- Имена файлов на диске — `uuid4().hex` (защита от path traversal).
- `MAX_CONTENT_LENGTH = 10 МБ` на загрузку шаблона.
- Валидация ключей полей: `^[a-z][a-z0-9_]{1,30}$`; дубликаты отсекаются
  UNIQUE-ограничением и перехватом `sqlite3.IntegrityError`.
- Все текстовые значения экранируются (`html.escape`) при генерации виджетов.
