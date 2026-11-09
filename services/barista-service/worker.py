"""barista-service: бере замовлення з черги, «готує» їх і зберігає в базу."""
import json
import os
import random
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone

import pika
from prometheus_client import Counter, Histogram, start_http_server

RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")
RABBIT_VHOST = os.getenv("RABBIT_VHOST", "/")
DB_PATH = os.getenv("DB_PATH", "/data/orders.db")
PREP_MIN = float(os.getenv("PREP_MIN_SECONDS", "2"))
PREP_MAX = float(os.getenv("PREP_MAX_SECONDS", "4"))

# Черга та її зв'язки створюються RabbitMQ із файлу rabbitmq/definitions.json.
QUEUE = "orders"

# Метрики, які забирає Prometheus.
ORDERS_PROCESSED = Counter(
    "coffee_orders_processed_total", "Скільки замовлень виконано", ["drink"]
)
for drink_name in ("espresso", "americano", "cappuccino", "latte", "tea"):
    ORDERS_PROCESSED.labels(drink=drink_name)  # щоб лічильник починався з нуля
ORDERS_FAILED = Counter(
    "coffee_orders_failed_total", "Скільки повідомлень відхилено через помилку формату"
)
ORDERS_DUPLICATE = Counter(
    "coffee_orders_duplicate_total", "Скільки повторних доставок пропущено"
)
PROCESSING_TIME = Histogram(
    "coffee_order_processing_seconds",
    "Час приготування одного замовлення",
    buckets=(1, 2, 3, 4, 5, 7, 10),
)

db = None


def init_db():
    """Створює таблицю замовлень, якщо її ще немає."""
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id TEXT PRIMARY KEY,
               drink TEXT NOT NULL,
               quantity INTEGER NOT NULL,
               customer TEXT,
               created_at TEXT,
               done_at TEXT NOT NULL
           )"""
    )
    connection.commit()
    return connection


def already_done(order_id):
    """Перевіряє, чи замовлення вже виконано (захист від повторної доставки)."""
    return db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone() is not None


def save_order(order_id, order, created_at):
    db.execute(
        "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?)",
        (
            order_id,
            order["drink"],
            order["quantity"],
            order.get("customer"),
            created_at,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    db.commit()


def parse_message(body):
    """Перевіряє повідомлення за контрактом. Якщо воно неправильне, викликає ValueError."""
    message = json.loads(body)
    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
        raise ValueError("повідомлення має містити об'єкт data")

    order_id = message.get("id")
    created_at = message.get("time")
    order = message["data"]

    if not isinstance(order_id, str) or not order_id:
        raise ValueError("поле id має бути непорожнім рядком")
    if not isinstance(order.get("drink"), str) or not order["drink"]:
        raise ValueError("поле drink має бути непорожнім рядком")
    if type(order.get("quantity")) is not int or order["quantity"] < 1:
        raise ValueError("поле quantity має бути цілим числом від 1")
    if not isinstance(order.get("customer"), (str, type(None))):
        raise ValueError("поле customer має бути рядком")
    if not isinstance(created_at, (str, type(None))):
        raise ValueError("поле time має бути рядком")

    return order_id, order, created_at


def handle_order(channel, method, properties, body):
    """Обробляє одне повідомлення з черги."""
    try:
        order_id, order, created_at = parse_message(body)
    except ValueError as error:
        # Зіпсоване повідомлення не повертаємо в чергу orders:
        # RabbitMQ перекладе його в окрему чергу orders.invalid для аналізу.
        ORDERS_FAILED.inc()
        print(f"Відхилено повідомлення: {error!r}")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    if already_done(order_id):
        ORDERS_DUPLICATE.inc()
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    with PROCESSING_TIME.time():
        time.sleep(random.uniform(PREP_MIN, PREP_MAX))  # імітація приготування

    save_order(order_id, order, created_at)
    ORDERS_PROCESSED.labels(drink=order["drink"]).inc()

    # Підтвердження надсилаємо лише після збереження: при збої замовлення не загубиться.
    channel.basic_ack(delivery_tag=method.delivery_tag)
    print(f"Готово: {order['drink']} x{order['quantity']} ({order_id})")


def consume():
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBIT_HOST, virtual_host=RABBIT_VHOST, credentials=credentials
    )

    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.basic_qos(prefetch_count=1)  # бариста бере по одному замовленню
    channel.basic_consume(queue=QUEUE, on_message_callback=handle_order)

    print("Бариста чекає на замовлення")
    channel.start_consuming()


def main():
    global db
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    db = init_db()
    start_http_server(8001)

    while True:
        try:
            consume()
        except (pika.exceptions.AMQPConnectionError, OSError):
            # OSError охоплює випадок, коли адресу rabbitmq неможливо знайти в мережі Docker.
            print("RabbitMQ недоступний, нова спроба через 3 секунди")
            time.sleep(3)


if __name__ == "__main__":
    main()
