"""Показує останні виконані замовлення з бази."""
import os
import sqlite3

connection = sqlite3.connect(os.getenv("DB_PATH", "/data/orders.db"))
total = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
rows = connection.execute(
    "SELECT done_at, drink, quantity, customer FROM orders ORDER BY done_at DESC LIMIT 20"
).fetchall()

print(f"Усього виконано: {total}")
for done_at, drink, quantity, customer in rows:
    print(f"{done_at}  {drink:<12} x{quantity}  {customer}")
