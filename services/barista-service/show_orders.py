"""Показує останні виконані замовлення з PostgreSQL."""
import os

import psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    total = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    rows = connection.execute(
        "SELECT done_at, drink, quantity, customer FROM orders ORDER BY done_at DESC LIMIT 20"
    ).fetchall()

print(f"Усього виконано: {total}")
for done_at, drink, quantity, customer in rows:
    print(f"{done_at}  {drink:<12} x{quantity}  {customer}")
