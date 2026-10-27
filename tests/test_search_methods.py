import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

# Проверим как разные методы поиска работают с кириллицей
search_terms = ["т", "ту", "туга", "vitug"]

print("=== Тест разных методов поиска ===\n")

for term in search_terms:
    print(f"Поиск: '{term}'")
    pattern = f"%{term}%"
    
    # Метод 1: LIKE (чувствителен к регистру)
    cursor.execute("SELECT first_name, last_name FROM persons WHERE first_name LIKE ?", (pattern,))
    count1 = len(cursor.fetchall())
    print(f"  LIKE: {count1} результатов")
    
    # Метод 2: LIKE COLLATE NOCASE
    cursor.execute("SELECT first_name, last_name FROM persons WHERE first_name LIKE ? COLLATE NOCASE", (pattern,))
    count2 = len(cursor.fetchall())
    print(f"  LIKE COLLATE NOCASE: {count2} результатов")
    
    # Метод 3: LOWER() both sides
    cursor.execute("SELECT first_name, last_name FROM persons WHERE LOWER(first_name) LIKE LOWER(?)", (pattern,))
    count3 = len(cursor.fetchall())
    print(f"  LOWER(both): {count3} результатов")
    
    # Метод 4: UPPER() both sides  
    cursor.execute("SELECT first_name, last_name FROM persons WHERE UPPER(first_name) LIKE UPPER(?)", (pattern,))
    count4 = len(cursor.fetchall())
    print(f"  UPPER(both): {count4} результатов")
    
    # Метод 5: Unicode casefold
    cursor.execute("SELECT first_name, last_name FROM persons WHERE UNICODE_CASEFOLD(first_name) LIKE UNICODE_CASEFOLD(?)", (pattern,))
    try:
        count5 = len(cursor.fetchall())
        print(f"  UNICODE_CASEFOLD: {count5} результатов")
    except Exception as e:
        print(f"  UNICODE_CASEFOLD: не поддерживается ({type(e).__name__})")
    
    print()

conn.close()
