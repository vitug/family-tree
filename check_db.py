import sqlite3

conn = sqlite3.connect('family_tree.db')
cursor = conn.cursor()

cursor.execute('PRAGMA encoding')
print('Database encoding:', cursor.fetchone()[0])

cursor.execute('SELECT first_name, last_name FROM persons LIMIT 5')
print('Sample names:')
for row in cursor.fetchall():
    print(f'  {row[0]} {row[1]}')

# Test search with Cyrillic (SQLite doesn't support ILIKE, so we'll use LIKE with COLLATE NOCASE)
cursor.execute("SELECT first_name, last_name FROM persons WHERE first_name LIKE '%Евгений%' COLLATE NOCASE")
print('\nSearch for Евгений:')
for row in cursor.fetchall():
    print(f'  {row[0]} {row[1]}')

conn.close()
