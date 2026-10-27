import sqlite3

def cyrillic_lower(text: str | None) -> str | None:
    """Функция для корректного перевода кириллицы в нижний регистр."""
    if text is None:
        return None
    return text.lower()

# Тест с пользовательской функцией
conn = sqlite3.connect('family_tree.db')
conn.create_function("cyrillic_lower", 1, cyrillic_lower)
cursor = conn.cursor()

print("=== Тест с пользовательской функцией cyrililc_lower ===\n")

search_terms = ["туга", "Туга", "vitug", "РИ", "ол"]

for term in search_terms:
    pattern = f"%{term}%"
    lower_pattern = pattern.lower()
    
    # Используем пользовательскую функцию
    cursor.execute("""
        SELECT first_name, last_name 
        FROM persons 
        WHERE cyrillic_lower(first_name) LIKE ? 
           OR cyrillic_lower(last_name) LIKE ?
    """, (lower_pattern, lower_pattern))
    
    results = cursor.fetchall()
    print(f"Поиск '{term}': {len(results)} результатов")
    for row in results[:3]:
        print(f"  {row[0]} {row[1]}")
    print()

conn.close()
