import sqlite3

conn = sqlite3.connect("vaultis.db")
cursor = conn.cursor()

# ดูก่อน
cursor.execute("SELECT * FROM investment_goals")
rows = cursor.fetchall()
print("Goals:", rows)

# ลบ goal ที่ target_date ไม่ใช่ date จริง
cursor.execute("DELETE FROM investment_goals WHERE target_date = 'string'")
conn.commit()
print("Deleted:", cursor.rowcount, "rows")

conn.close()
