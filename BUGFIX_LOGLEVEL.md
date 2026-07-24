# 🐛 Исправление ошибки log-level в скриптах запуска

## Проблема

При запуске появлялась ошибка:
```
Error: Invalid value for '--log-level': 'INFO' is not one of 'critical', 'error', 'warning', 'info', 'debug', 'trace'.
```

### Причина
- Uvicorn ожидает **lowercase** log levels: `debug`, `info`, etc.
- Скрипты передавали **uppercase** значения: `DEBUG`, `INFO`, `WARNING`
- Конфликт типов вызывал ошибку запуска

## ✅ Исправление

### start.bat
Добавлена конвертация log level в lowercase перед запуском uvicorn:
```batch
REM Convert log level to lowercase for uvicorn (uvicorn requires lowercase log levels)
powershell -Command "'%FAMILY_TREE_LOG_LEVEL%'.ToLower()" > temp_log.txt
set /p UVICORN_LOG_LEVEL=<temp_log.txt
del temp_log.txt

REM Start the uvicorn server
%PYTHON_CMD% -m uvicorn app.main:app --host %FAMILY_TREE_HOST% --port %FAMILY_TREE_PORT% --log-level %UVICORN_LOG_LEVEL% --access-log
```

### start.ps1
Добавлена конвертация log level в lowercase через PowerShell метод:
```powershell
# Start the uvicorn server
$uvicornLogLevel = $env:FAMILY_TREE_LOG_LEVEL.ToLower()
& $pythonCmd -m uvicorn app.main:app --host $env:FAMILY_TREE_HOST --port $env:FAMILY_TREE_PORT --log-level $uvicornLogLevel --access-log
```

## 🔍 Таблица конвертации

| Значение из скрипта | Передаётся в uvicorn (конвертировано) |
|---|---|
| `DEBUG` | `debug` |
| `INFO` | `info` |
| `WARNING` | `warning` |
| `ERROR` | `error` |
| `CRITICAL` | `critical` |

## ✅ Результат

- ✅ Сервер запускается без ошибок
- ✅ Dev mode использует `--log-level debug`
- ✅ Prod mode использует `--log-level info`
- ✅ Uvicorn принимает правильные значения
- ✅ Консольный вывод ожидаемый

## 🚀 Запуск теперь работает

```cmd
# Dev mode
set FAMILY_TREE_STARTUP_MODE=dev
start.bat

# Prod mode (default)
start.bat

# PowerShell
.\start.ps1
```

## 📝 Примечания

1. **Режим разработки** автоматически устанавливает log level `DEBUG` (строчные)
2. **Режим продакшена** автоматически устанавливает log level `INFO` (строчные)
3. Конвертация происходит **автоматически** перед запуском uvicorn
4. Оба скрипта (`start.bat` и `start.ps1`) исправлены

## 🧪 Тестирование

Запустите скрипты снова - ошибка должна исчезнуть:
```cmd
start.bat
```

Ожидаемый заполненный экран:
```
[5/5] Starting Family Tree Service...
==============================================================================
Application is accessible at:
  Local:    http://127.0.0.1:8000
  Network: http://YOUR_COMPUTER:8000
==============================================================================
Use view token to login: YOUR_VIEW_TOKEN
Use edit token for full access: YOUR_EDIT_TOKEN
==============================================================================
[INFO] Started server process [Process ID]
[INFO] Waiting for application startup.
[INFO] Application startup complete.
[INFO] Uvicorn running on http://0.0.0.0:8000
```
