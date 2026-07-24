import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

print("=== Проверка содержимого базы данных ===\n")

# Посмотрим на все имена "vitug"
cursor.execute("SELECT id, first_name, last_name FROM persons WHERE last_name LIKE '%угари%'" )
results = cursor.fetchall()
print(f"Имена с 'угари': {len(results)} результатов")
for row in results:
    print(f"  ID {row[0]}: {repr(row[1])} {repr(row[2])}")
    # Посмотрим HEX представление
    print(f"    HEX first_name: {row[1].encode('utf-8').hex()}")
    print(f"    HEX last_name: {row[2].encode('utf-8').hex()}")

print("\n=== Все имена в базе ===")
cursor.execute("SELECT first_name, last_name FROM persons LIMIT 10")
for row in cursor.fetchall():
    print(f"{repr(row[0])} {repr(row[1])}")

# Прямой тест для "туга"
print("\n=== Тест поиска 'туга' ===")
cursor.execute("SELECT first_name, last_name FROM persons WHERE last_name LIKE ?", ('%туга%',))
print(f"LIKE '%туга%': {len(cursor.fetchall())} результатов")

# С маленькими буквами
cursor.execute("SELECT first_name, last_name FROM persons WHERE LOWER(last_name) LIKE LOWER(?)", ('%туга%',))
print(f"LOWER('%туга%'): {len(cursor.fetchall())} результатов")

# С заглавными буквами
cursor.execute("SELECT first_name, last_name FROM persons WHERE last_name LIKE ?", ('%ТУГА%',))
print(f"LIKE '%ТУГА%': {len(cursor.fetchall())} результатов")

conn.close()
