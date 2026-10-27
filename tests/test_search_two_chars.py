import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

# Тестируем именно для двух букв
terms = ["ту", "га", "ри"]

print("=== Тест для двух букв ===\n")

for term in terms:
    print(f"Поиск: '{term}'")
    pattern = f"%{term}%"
    
    # Посмотрим что именно есть в базе для теста
    cursor.execute("SELECT first_name, last_name FROM persons LIMIT 5")
    print("Примеры имен в базе:")
    for row in cursor.fetchall():
        print(f"  {row[0]} {row[1]}")
    
    # Метод 1: LIKE COLLATE NOCASE  
    cursor.execute("SELECT first_name, last_name FROM persons WHERE first_name LIKE ? COLLATE NOCASE", (pattern,))
    results1 = cursor.fetchall()
    print(f"LIKE COLLATE NOCASE: {len(results1)} результатов:")
    for row in results1[:3]:  # Показать первые 3
        print(f"  {row[0]} {row[1]}")
    
    # Метод 2: LOWER() both sides
    cursor.execute("SELECT first_name, last_name FROM persons WHERE LOWER(first_name) LIKE LOWER(?)", (pattern,))
    results2 = cursor.fetchall()
    print(f"LOWER(both): {len(results2)} результатов:")
    for row in results2[:3]:
        print(f"  {row[0]} {row[1]}")
    
    # Метод 3: Проверим что происходит с паттерном
    print(f"Паттерн для LOWER(): {pattern.lower()}")
    
    # Попробуем найти что-нибудь с "ту" в имени
    cursor.execute("SELECT first_name, last_name FROM persons WHERE first_name LIKE '%ту%'")
    results_direct = cursor.fetchall()
    print(f"Прямой запрос LIKE '%ту%': {len(results_direct)} результатов")
    for row in results_direct[:3]:
        print(f"  {row[0]} {row[1]}")
    
    print()

conn.close()
