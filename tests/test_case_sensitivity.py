import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

print("=== Проверка чувствительности к регистру ===\n")

# Сравниваем hex коды
print("HEX коды:")
print("  Заглавная Т: 'Т'.encode('utf-8').hex() =", 'Т'.encode('utf-8').hex())
print("  Маленькая т: 'т'.encode('utf-8').hex() =", 'т'.encode('utf-8').hex())
print("  Заглавная У: 'У'.encode('utf-8').hex() =", 'У'.encode('utf-8').hex())
print("  Маленькая у: 'у'.encode('utf-8').hex() =", 'у'.encode('utf-8').hex())

print("\n=== Поиск с разными регистрами ===")

# Поиск с заглавными буквами
cursor.execute("SELECT first_name, last_name FROM persons WHERE last_name LIKE ?", ('%ТУГА%',))
results_upper = cursor.fetchall()
print(f"LIKE '%ТУГА%': {len(results_upper)} результатов")

# Поиск с маленькими буквами
cursor.execute("SELECT first_name, last_name FROM persons WHERE last_name LIKE ?", ('%туга%',))
results_lower = cursor.fetchall()
print(f"LIKE '%туга%': {len(results_lower)} результатов")

# Поиск с COLLATE NOCASE
cursor.execute("SELECT first_name, last_name FROM persons WHERE last_name LIKE ? COLLATE NOCASE", ('%туга%',))
results_nocase = cursor.fetchall()
print(f"LIKE '%туга%' COLLATE NOCASE: {len(results_nocase)} результатов")

# ПопробуемLOWER()
cursor.execute("SELECT first_name, last_name FROM persons WHERE LOWER(last_name) LIKE LOWER(?)", ('%ТУГА%',))
results_lower_both = cursor.fetchall()
print(f"LOWER(last_name) LIKE LOWER('%ТУГА%'): {len(results_lower_both)} результатов")

# Сканирование всей базы если LOWER не работает
print("\n=== Сканирование базы для поиска 'туга' ===")
all_people = cursor.execute("SELECT first_name, last_name FROM persons").fetchall()
matches = []
for first, last in all_people:
    full_name = f"{first or ''} {last or ''}".lower()
    if 'туга' in full_name:
        matches.append((first, last))
print(f"Ручной поиск 'туга' (нечувствительный к регистру): {len(matches)} результатов")
for match in matches[:3]:
    print(f"  {match[0]} {match[1]}")

# Проверим что происходит при переводе в LOWER
print("\n=== Проверка LOWER() в SQLite ===")
test_name = "vitug"
cursor.execute("SELECT LOWER(first_name) FROM persons WHERE last_name = ?", (test_name,))
lower_test = cursor.fetchone()
if lower_test:
    print(f"LOWER('vitug') в SQLite: {repr(lower_test[0])}")

conn.close()
