# Запуск Family Tree Service

## Обновлённые скрипты запуска

Какие улучшения были добавлены в `start.bat` и `start.ps1`:

### ✨ Новые возможности

#### 1. **Интерактивный выбор режима запуска**
```
[Startup Mode]
  1 - Development (debug logging, minimal security)
  2 - Production (info logging, full security)
```

#### 2. **Автоматическая генерация .env**
- Если `.env` отсутствует → создаётся с сильными токенами
- Использует `secrets.token_urlsafe()` для crytographically secure токенов
- Автоматически настраивает базовую защиту

#### 3. **Проверка безопасности токенов**
- Для dev-mode: предупреждает о дефолтных токенах
- Для prod-mode: требует сильные токены (минимум 12 символов)
- Проверяет версию Python (минимум 3.9)

#### 4. **Проверка доступности порта**
- Автоматически проверяет, занят ли порт 8000
- Предлагает продолжить или прервать запуск
- Уведомляет о потенциальных конфликтах

#### 5. **Маскировка токенов**
- Токены отображаются в логахMAKSED (первые 10 + последние 4 символа)
- Полные значения сохраняются только в созданном `.env`

#### 6. **Подготовка директорий**
- Автоматически создаёт `logs/`
- Автоматически создаёт `media/` и `media/files/`
- Проверяет на наличие `app/security.py`

### 🎯 Поддерживаемые окружающие переменные

```bash
# Режим запуска (опционально)
FAMILY_TREE_STARTUP_MODE=dev|prod

# Переопределение настроек из .env
FAMILY_TREE_HOST=0.0.0.0
FAMILY_TREE_PORT=8000
FAMILY_TREE_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR
FAMILY_TREE_PROTECT_API_DOCS=true|false
```

### 📋 Шаги запуска скрипта

1. **Размещение**: Скрипты (`start.bat` и `start.ps1`) должны быть в корне проекта
2. **Python**: Автоматически ищет Python (python, py, python3)
3. **Dependencies**: Устанавливает зависимости из `requirements.txt`
4. **Environment**: Проверяет наличие `.env`, генерирует при отсутствии
5. **Startup**: Запускает Uvicorn сервер

### 🖥️ Примеры использования

#### Базовый запуск (про режим)
```cmd
start.bat
```

#### Режим разработки
```cmd
set FAMILY_TREE_STARTUP_MODE=dev
start.bat
```

#### PowerShell с выбором режима
```powershell
.\start.ps1
```

#### PowerShell автоматический prod режим
```powershell
$env:FAMILY_TREE_STARTUP_MODE = "prod"
.\start.ps1
```

### 🔒 Безопасность

#### Сгенерированные токены
- **JWT_SECRET_KEY**: 43 случайных символа (base64)
- **VIEW_TOKEN**: 18 случайных символов (base64)
- **EDIT_TOKEN**: 18 случайных символов (base64)

#### Настройки по умолчанию (если .env создан автоматически)
```bash
FAMILY_TREE_JWT_SECRET_KEY=<secure-random>
FAMILY_TREE_VIEW_TOKEN=<secure-random>
FAMILY_TREE_EDIT_TOKEN=<secure-random>
FAMILY_TREE_LOG_LEVEL=DEBUG|INFO
FAMILY_TREE_PROTECT_API_DOCS=true|false
FAMILY_TREE_ENABLE_BRUTE_FORCE_PROTECTION=true
FAMILY_TREE_ENABLE_SECURITY_HEADERS=true
FAMILY_TREE_ALLOW_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
```

### ⚠️ Важные примечания

1. **Первая установка**
   - Скрипт автоматически создаст `.env` с сильными токенами
   - **Скопируйте** токены в безопасное место
   - Не коммитите `.env` в репозиторий

2. **Уже есть .env**
   - Скрипт использует существующие токены
   - Отображает замаскированные значения в логах
   - Не перезаписывает существующий файл

3. **Конфликты портов**
   - Если порт 8000 занят → скрипт предложит выбор продолжить или прервать
   - Для тестов можно использовать другой порт через `FAMILY_TREE_PORT`

4. **Безопасность**
   - Запуск в **prod режиме** включает все защиты
   - Запуск в **dev режиме** упрощает отладку
   - Рекомендуется использовать prod для публичного доступа

### 📊 Проверки скрипта

Скрипт выполняет следующие проверки перед запуском:

1. ✅ Найти Python 3.9+
2. ✅ Проверить наличие `requirements.txt`
3. ✅ Установить зависимости
4. ✅ Проверить структуру приложения (`app/main.py`, `app/security.py`)
5. ✅ Проверить/создать `.env` с токенами
6. ✅ Подготовить директории (`logs`, `media`, `media/files`)
7. ✅ Проверить доступность порта
8. ✅ Запустить сервер uvicorn

### 🚀 Быстрый старт

```cmd
# Windows CMD
start.bat

# PowerShell
.\start.ps1

# Если Python не в PATH
py start.bat
```

### 🛠️ Устранение неполадок

**Python не найден**
```cmd
# Установите Python с python.org
# Добавьте Python в PATH
# Или используйте py launcher
```

**Порт занят**
```cmd
# Найдите процесс
netstat -ano | findstr :8000

# И используйте другой порт
set FAMILY_TREE_PORT=8001
start.bat
```

**Dependencies не устанавливаются**
```cmd
# Установите вручную
python -m pip install -r requirements.txt
```

### 📝 Примечания разработчика

- Скрипты test-friendly (можно запускать без интерактивности через `FAMILY_TREE_STARTUP_MODE`)
- Поддерживают UTF-8 для русских токенов
- Генерация токенов использует cryptographically secure source
- Поддерживают автоматическое переключение между dev/prod окружениями

## 🔗 Дополнительные файлы

- `.env.example` — пример конфигурации с документацией
- `SECURITY.md` — руководство по безопасности
- `SECURITY_CHECKLIST.md` — чеклист перед продакшеном
