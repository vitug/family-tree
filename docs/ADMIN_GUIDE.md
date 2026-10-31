# 🛠 Семейное дерево — руководство для администратора

Для того, кто разворачивает и обслуживает сервис. Пользовательское руководство —
в [USER_GUIDE.md](USER_GUIDE.md).

## Установка (Linux-сервер)

```bash
# Python 3.9+, лучше 3.11+
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # и заполнить (см. ниже)
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Тесты: `.venv/bin/python -m pytest tests/test_audit_fixes.py -q`.

## Конфигурация (.env)

Минимум для продакшена — сгенерируйте и сохраните:

```bash
FAMILY_TREE_JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
FAMILY_TREE_VIEW_TOKEN=$(openssl rand -base64 18)   # ссылка «только смотреть»
FAMILY_TREE_EDIT_TOKEN=$(openssl rand -base64 18)   # ссылка «редактировать»
```

- **JWT-секрет** — подпись сессий. Смена = разлогин всех.
- **VIEW/EDIT-токены** — «двери» приложения. Ссылка для входа:
  `https://<домен><root_path>api/v1/auth/link/<токен>` (302 → приложение, ставит
  httpOnly-cookie на месяц со слайдинг-продлением).
- `FAMILY_TREE_ROOT_PATH=/путь/` — если приложение живёт за reverse-proxy в
  подкаталоге (см. nginx ниже). Обязательно со слэшами на концах.
- `FAMILY_TREE_PROTECT_API_DOCS=true` — /docs и /openapi.json только для JWT.
- Полный список параметров — в `.env.example` с комментариями.

## Раздача ссылок

- Персональные ссылки на конкретного человека (с ограничением круга видимости):
  `POST /api/v1/persons/{id}/access-link` (нужен edit-токен), отзыв —
  `DELETE /api/v1/persons/access-links/{id}`.
- VIEW-токен — всем родственникам «по чтению»; EDIT — только доверенным.

## Nginx (reverse-proxy в подкаталоге-«двери»)

```nginx
location /fTk9abcX/ {                      # «дверь» — длинный неугадываемый путь
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

В `.env` тогда `FAMILY_TREE_ROOT_PATH=/fTk9abcX/`. Cookie ставится на Path
двери — несколько экземпляров (прод/дев) на одном домене не конфликтуют.
HTTPS обязателен (cookie Secure, PWA требует https).

## systemd

```ini
[Unit]
Description=Family Tree
After=network.target

[Service]
User=familytree
WorkingDirectory=/opt/family-tree
ExecStart=/opt/family-tree/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## Обслуживание

- **Бэкап**: SQLite-файл + `media/files/` + `.env`. Достаточно остановки нет —
  достаточно `sqlite3 .backup` или копии при низкой нагрузке.
- **Проверка целостности дерева** (циклы, даты, возраст родителей):
  `.venv/bin/python scripts/validate_tree.py --db family_tree.db`
  (exit 1 при ошибках; предупреждения — не блокирующие).
- **Демо-данные** (вымышленная семья «Тестовых» + иностранцы + поколение XIX века):
  `.venv/bin/python scripts/seed_demo.py --force`
- **Логи**: stdout (journalctl) + файлы в `logs/` с ротацией.
- **Медиа**: фото складываются в `media/files/<id человека>/`, автоматически
  делается миниатюра `t_*.jpg` (256px). Лимит файла — 10 МБ, формат проверяется
  по магическим байтам.

## Безопасность

- Вход только по ссылкам-токенам (brute-force защита: 3 попытки → 5 мин бана).
- Заметки для «читателей» маскируются: телефоны/e-mail/коды (ru+en, phonenumbers).
- Никогда не коммитьте `.env`; при утечке токена — смените его в `.env` и
  перезапустите сервис (все старые ссылки умрут).

## Desktop-сборка (Windows .exe)

Одноfile-исполняемый файл для запуска без Python — см.
[BUILD_DESKTOP.md](BUILD_DESKTOP.md).
