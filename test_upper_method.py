import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

print("=== Тест UPPER() вместо LOWER() ===\n")

search_term = "туга"
pattern = f"%{search_term}%"

# Метод 1: UPPER() both sides
cursor.execute("SELECT first_name, last_name FROM persons WHERE UPPER(last_name) LIKE UPPER(?)", (pattern,))
results_upper = cursor.fetchall()
print(f"UPPER(last_name) LIKE UPPER('%{search_term}%'): {len(results_upper)} результатов")
for row in results_upper[:3]:
    print(f"  {row[0]} {row[1]}")

# Проверим что делает UPPER() с кириллицей
print("\n=== Проверка UPPER() в SQLite ===")
cursor.execute("SELECT UPPER(first_name), UPPER(last_name) FROM persons WHERE id = 1")
upper_result = cursor.fetchone()
print(f"UPPER('Евгений vitug') в SQLite: {repr(upper_result)}")

# Сравним с Python
print(f"UPPER('Евгений') в Python: {'Евгений'.upper()}")
print(f"UPPER('vitug') в Python: {'vitug'.upper()}")

# Протестируем разные варианты
for term in ["то", "ГА", "РИ", "ха"]:
    pattern = f"%{term}%"
    cursor.execute("SELECT first_name, last_name FROM persons WHERE UPPER(last_name) LIKE UPPER(?)", (pattern,))
    results = cursor.fetchall()
    print(f"UPPER('%{term}%'): {len(results)} результатов")

conn.close()
