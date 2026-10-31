import requests

# Сначала получим токен авторизации
auth_response = requests.post(
    "http://127.0.0.1:8000/api/v1/auth/token",
    json={"token": "family"},
    headers={"Content-Type": "application/json"}
)

if auth_response.status_code == 200:
    token_data = auth_response.json()
    access_token = token_data["access_token"]
    print(f"✓ Получен токен авторизации: {token_data['role']}")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Протестируем поиск с кириллицей
    test_queries = [
        "Евгений",
        "vitug",
        "Мария",
        "Холмс"
    ]

    for query in test_queries:
        print(f"\n--- Поиск: '{query}' ---")

        # Кодируем параметр запроса вручную для теста
        from urllib.parse import urlencode
        params = urlencode({"q": query})

        search_response = requests.get(
            f"http://127.0.0.1:8000/api/v1/persons?{params}",
            headers=headers
        )

        if search_response.status_code == 200:
            people = search_response.json()
            print(f"Найдено: {len(people)} результатов")
            for person in people[:3]:  # Покажем первые 3 результата
                print(f"  - {person['full_name']} (ID: {person['id']})")
        else:
            print(f"Ошибка: {search_response.status_code}")
            print(f"Ответ: {search_response.text}")
else:
    print(f"Ошибка авторизации: {auth_response.status_code}")
    print(f"Ответ: {auth_response.text}")
