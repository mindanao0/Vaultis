import sqlite3

conn = sqlite3.connect("vaultis.db")
cursor = conn.cursor()

# ดู tables ทั้งหมด
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("=== Tables ===")
for t in tables:
    print("-", t[0])
print()

# ดูข้อมูลแต่ละ table
for (table,) in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"{table}: {count} rows")

conn.close()
