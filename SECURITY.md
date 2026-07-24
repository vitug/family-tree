# Безопасность приложения: рекомендации и加固

## 🛡️ Обзор введённых улучшений

### До (что было)
- ❌ **Нет rate limiting** — можно постоянно долбить `/auth/token` с разными токенами
- ❌ **Нет brute-force защиты** — попытки входа не лимитируются по количеству
- ❌ **Медиа файлы открыты** — `/media/files/{person_id}/{filename}` доступен без авторизации
- ❌ **API docs открыты** — `/docs` и `/openapi.json` показывают схему без токена
- ❌ **Нет CORS настройки** — все origins разрешены по умолчанию
- ❌ **Слабые токены по умолчанию** — `family`, `secret` легко угадать
- ❌ **Нет security headers** — отсутствуют CSP, XSS Protection и т.д.
- ❌ **JWT secret меняется при каждом запуске** — ломает сессии пользователей

### После (что стало)
- ✅ **Rate limiting** — 5 попыток входа в минуту на IP
- ✅ **Brute-force временная блокировка** — 3 неудачные попытки → 5 минут бана
- ✅ **Медиа файлы защищены** — эндпоинт требует JWT токен
- ✅ **API docs защищены** — `/docs` требует авторизацию
- ✅ **CORS настроен** — явно указаны разрешённые origins
- ✅ **Валидация сложности токенов** — минимум 12 символов, проверка на стандартные
- ✅ **Security headers** — CSP, XSS Protection, Frame Options и т.д.
- ✅ **JWT secret из .env** — стабильный между запусками

## 📦 Установка и настройка

### 1. Создайте `.env` файл из примера
```bash
cp .env.example .env
```

### 2. Сгенерируйте сильные секреты (обязательно!)
```bash
# JWT secret (минимум 32 символа)
python -c "import secrets; print('FAMILY_TREE_JWT_SECRET_KEY=' + secrets.token_urlsafe(32))" >> .env

# View token (минимум 12 символов, лучше 24+)
python -c "import secrets; print('FAMILY_TREE_VIEW_TOKEN=' + secrets.token_urlsafe(18))" >> .env

# Edit token (минимум 12 символов)
python -c "import secrets; print('FAMILY_TREE_EDIT_TOKEN=' + secrets.token_urlsafe(18))" >> .env
```

### 3. Настройте CORS origins для продакшена
В `.env`:
```
FAMILY_TREE_ALLOW_CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

### 4. Переместите приложение за reverse proxy (рекомендуется)
Используйте **Nginx** или **Traefik** для:
- Termination SSL/TLS
- Логирования реальных IP (`X-Forwarded-For`, `X-Real-IP`)
- Лимитов на уровне прокси (двойная защита)

Пример Nginx конфигурации:
```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Content-Type-Options "nosniff";

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Rate limiting на уровне Nginx (дополнительная защита)
        limit_req zone=auth zone=one:10m burst=20 nodelay;
    }

    # Отдельный лимит для /auth
    location /api/v1/auth/token {
        proxy_pass http://127.0.0.1:8000;
        limit_req zone=auth burst=5 nodelay;
    }

    location /media/ {
        # Запрещаем доступ к медиа без авторизации на уровне Nginx
        internal;  # только internal redirect
    }
}
```

## 🔒 Настройки безопасности в `.env`

### Обязательные (для продакшена)
- `FAMILY_TREE_JWT_SECRET_KEY` — минимум 32 случайных символа
- `FAMILY_TREE_VIEW_TOKEN` — минимум 12 символов, лучше 24+
- `FAMILY_TREE_EDIT_TOKEN` — минимум 12 символов, лучше 24+
- `FAMILY_TREE_ALLOW_CORS_ORIGINS` — только ваши домены

### Настройки защиты
- `FAMILY_TREE_ENABLE_RATE_LIMITING=true` — включить rate limiting (5 запросов/мин для /auth)
- `FAMILY_TREE_ENABLE_BRUTE_FORCE_PROTECTION=true` — включить brute-force защиту (3 попытки → 5 мин бана)
- `FAMILY_TREE_ENABLE_SECURITY_HEADERS=true` — добавлять security headers
- `FAMILY_TREE_PROTECT_API_DOCS=true` — требовать JWT для /docs

## 🧪 Проверка безопасности

### 1. Проверьте защиту от перебора
```bash
# Попробуйте 3+ раза с неверным токеном
for i in {1..5}; do
  curl -X POST http://localhost:8000/api/v1/auth/token \
    -H "Content-Type: application/json" \
    -d '{"token": "wrong-'$i'"}'
done
# После 3 попыток должны получить 403 Forbidden
```

### 2. Попробуйте открыть API docs без авторизации
```bash
# Должен вернуть index.html (перенаправление) или 401/403
curl -I http://localhost:8000/docs
```

### 3. Проверьте доступ к медиа без токена
```bash
# Замените ID на существующий
curl -I http://localhost:8000/media/files/1/some-photo.jpg
# Должен вернуть 401 Unauthorized
```

### 4. Проверьте заголовки безопасности
```bash
curl -I http://localhost:8000/
# Должны присутствовать:
# X-Frame-Options: DENY
# X-XSS-Protection: 1; mode=block
# X-Content-Type-Options: nosniff
# Content-Security-Policy: ...
```

## 🔑 Управление токенами

### Смена токенов
```bash
# Сгенерируйте новые и поместите в .env
# После перезагрузки приложения все JWT токены станут недействительными
```

### Стратегии сессий
- JWT токены живут 24 часа (по умолчанию)
- При смене `FAMILY_TREE_JWT_SECRET_KEY` ломаются ВСЕ сессии пользователей
- Для отзывания конкретных токенов внедрите blacklist в Redis (не реализовано в этом MVP)

## 📝 Audit checklist для продакшена

- [ ] Создан `.env` файл из `.env.example`
- [ ] Сгенерированы **сильные** секреты (32+ символов)
- [ ] Установлен `FAMILY_TREE_VIEW_TOKEN` и `FAMILY_TREE_EDIT_TOKEN`
- [ ] Настроен `FAMILY_TREE_ALLOW_CORS_ORIGINS` (только ваши домены)
- [ ] Включен SSL/TLS (reverse proxy)
- [ ] Включены все защиты (`ENABLE_RATE_LIMITING`, `ENABLE_BRUTE_FORCE_PROTECTION`, etc.)
- [ ] Настроен backup базы данных
- [ ] Приложение запущено под ограниченным пользователем (не root)
- [ ] Логи ротируются (logrotate)
- [ ] Firewall ограничивает доступ к порту 8000

## 🛠️ Further improvements (не реализовано в этом MVP)

- **Redis-based rate limiting** — более надёжная защита от per-IP лимитов
- **Token blacklist** — для отзывания скомпрометированных JWT
- **MFA/TOTP** — двухфакторная аутентификация
- **IP whitelisting** — разрешённые IP для админ доступа
- **Honeypot endpoints** — для обнаружения сканеров
- **WAF integration** — Web Application Firewall (ModSecurity)

## 🚨 Что делать, если обнаружена атака

### Если видите много 401 в логах
```bash
# Проверьте access.log
grep "401" logs/access.log | awk '{print $1}' | sort -u > attackers.txt

# Блокируйте в firewall (пример IPTABLES)
while read ip; do
  iptables -A INPUT -s $ip -j DROP
done < attackers.txt
```

### Если забыли токен
```bash
# Остановите приложение
# Отредактируйте .env и задайте новые значения
# Запустите снова
```

## 🤝 Contributing к безопасности

При изменении кода, связанного с аутентификацией:
1. Все изменения должны быть backward compatible
2. Добавляйте тесты для новых защит
3. Обновляйте `SECURITY.md` описанием новых механизмов
4. Используйте `secrets.token_urlsafe()` вместо `random.choices()`
