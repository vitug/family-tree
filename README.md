# Family Tree Service 🌳

Сервис для управления семейным деревом с REST API, поддержкой импорта из Gramps и экспортом в Obsidian.

## 🚀 Быстрый старт

### Windows

#### Для обычного использования:
```bash
# cmd/command prompt
start.bat

# PowerShell
.\start.ps1
```

#### Для разработки (с авто-перезагрузкой):
```bash
# cmd/command prompt  
dev.bat

# PowerShell
.\dev.ps1
```

### Linux/Mac

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 📋 Системные требования

- Python 3.9+
- Доступ к интернету для установки зависимостей

## 🔐 Аутентификация

В проекте внедрена **JWT аутентификация** с истечением срока действия токенов.

### Начальный доступ

Есть два способа получить доступ без ввода токенов:

**1. Ссылки-ключи (рекомендуется для семьи).** Сгенерируй два длинных адреса:

```bash
python scripts/generate_access_links.py --base-url http://<адрес-сервера>:8000
```

Скрипт создаст (при необходимости) длинные случайные токены в `.env` и напечатает:

- 🔗 **только чтение** — `http://host:8000/api/v1/auth/link/<view-token>`
- ✏️ **редактирование** — `http://host:8000/api/v1/auth/link/<edit-token>`

Открытие ссылки ставит httpOnly cookie с JWT (24 ч, продлевается автоматически) и редиректит на приложение. Вводить ничего не нужно. Ссылки равносильны паролям — передавать лично. `--force` перегенерирует токены (все старые ссылки отзовутся), `--show` печатает текущие.

**2. Вручную через веб-интерфейс:**

1. Войдите в веб-интерфейс: http://localhost:8000
2. Нажмите на кнопку 🔐 в правом верхнем углу
3. Введите токен редактирования: `secret-edit-token-dev` (или заданный в .env)
4. Получите JWT токен для доступа к защищенным операциям

### API с JWT токеном

#### Получение токена:
```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"edit_token": "secret"}'
```

#### Использование токена:
```bash
curl http://localhost:8000/api/v1/persons \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### Обновление токена:
```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"token": "YOUR_JWT_TOKEN"}'
```

### Конфигурация JWT

Настройте параметры через переменные окружения:

```bash
# Секретный ключ (обязательно настройте для production!)
export FAMILY_TREE_JWT_SECRET_KEY="your-secure-secret-key"

# Срок действия токена (часы)
export FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS=24

# Алгоритм подписи
export FAMILY_TREE_JWT_ALGORITHM=HS256
```

## 🌐 API Документация

После запуска сервера доступна интерактивная документация:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 📦 Основные возможности

### Люди
- ✅ Создание, редактирование, удаление записей
- ✅ Поиск по имени/фамилии
- ✅ Загрузка фотографий
- ✅ Подробная информация (даты рождения/смерти, место рождения и др.)

### Родственные связи
- ✅ Родительские связи (`parent_of`)
- ✅ Супружеские связи (`spouse_of`)
- ✅ Поиск пути родства
- ✅ Автоматическое предотвращение циклов

### Семейное дерево
- ✅ Построение дерева вокруг выбранного человека
- ✅ Предки и потомки по поколениям
- ✅ Ближайшие родственники
- ✅ Настройка глубины отображения

### Импорт/Экспорт
- ✅ Импорт из Gramps (.gpkg, .gramps)
- ✅ Загрузка фотографий при импорте
- ✅ Экспорт в Obsidian Markdown
- ✅ Генерация ZIP архива для загрузки

## ⚙️ Конфигурация

Все настройки могут быть переопределены через переменные окружения:

```bash
# База данных
export FAMILY_TREE_DATABASE_URL="sqlite:///./family_tree.db"

# JWT аутентификация (ОБЯЗАТЕЛЬНО: без FAMILY_TREE_JWT_SECRET_KEY сервис не стартует;
# минимум 32 символа)
export FAMILY_TREE_JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS=24

# Legacy токен (для обратной совместимости; минимум 12 символов)
export FAMILY_TREE_EDIT_TOKEN="secret-edit-token-dev"

# Логирование
export FAMILY_TREE_LOG_LEVEL="INFO"

# Сервер
export FAMILY_TREE_HOST="0.0.0.0"
export FAMILY_TREE_PORT="8000"
```

Пример файла конфигурации смотрите в `.env.example`.

## 🛠️ Разработка

### Установка окружения

```bash
# Клонирование репозитория
git clone <repository-url>
cd grok

# Установка зависимостей
pip install -r requirements.txt

# Запуск в режиме разработки
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Структура проекта

```
grok/
├── app/                    # Основное приложение
│   ├── main.py            # Точка входа
│   ├── config.py          # Конфигурация
│   ├── database.py        # База данных
│   ├── models.py          # SQLAlchemy модели
│   ├── schemas.py         # Pydantic схемы
│   ├── routers/           # API роутеры
│   ├── services/          # Бизнес-логика
│   ├── static/            # Статические файлы
│   └── utils/             # Утилиты
├── media/                 # Медиа файлы
├── export/                # Экспортированные данные
├── logs/                  # Логи приложения
├── requirements.txt       # Зависимости
├── start.bat             # Запуск (Windows)
├── start.ps1             # Запуск (PowerShell)
├── dev.bat               # Режим разработки (Windows)
├── dev.ps1               # Режим разработки (PowerShell)
└── .env.example          # Пример конфигурации
```

## 🔒 Безопасность

### Рекомендации для production:

1. **Настройте секретный ключ JWT:**
   ```bash
   # Генерация безопасного ключа
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Измените edit token по умолчанию:**
   ```bash
   export FAMILY_TREE_EDIT_TOKEN="your-secure-token"
   ```

3. **Используйте HTTPS:**
   - Настройте reverse proxy (nginx, caddy)
   - Используйте SSL сертификаты

4. **Ограничьте доступ:**
   - Настройте firewall правила
   - ИспользRate limiting
   - Добавьте 2FA для критических операций

5. **Резервное копирование:**
   - Регулярно копируйте базу данных
   - Бэкапьте загруженные медиа файлы

## 🐛 Устранение проблем

### Python не найден

**Проблема:** Скрипт не может найти Python

**Решение:**
1. Убедитесь, что Python установлен: `python --version`
2. Добавьте Python в PATH
3. Или укажите полный путь в скрипте запуска

### Ошибка при установке зависимостей

**Проблема:** Пакеты не устанавливаются

**Решение:**
```bash
# Попробуйте без quiet флага
pip install -r requirements.txt

# Обновите pip
pip install --upgrade pip
```

### Сервер не запускается

**Проблема:** Ошибка при запуске uvicorn

**Решение:**
1. Проверьте, что порт 8000 свободен
2. Посмотрите детальные логи с `--log-level debug`
3. Проверьте структуру приложения

## 🖥 Desktop-версия (Windows .exe)

Однофайловый `FamilyTree.exe` без установленного Python — двойной клик, сервер
поднимается локально и открывается браузер:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller family_tree.spec
```

Подробности — [docs/BUILD_DESKTOP.md](docs/BUILD_DESKTOP.md).

## 📚 Документация

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — для пользователей семьи
- [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) — установка, настройка, эксплуатация

## 📝 Лицензия

MIT — см. [LICENSE](LICENSE).

## 🤝 Поддержка

Для вопросов и предложений:
- GitHub Issues
- Документация API: http://localhost:8000/docs

---

**Enjoy building your family tree! 🌳**
