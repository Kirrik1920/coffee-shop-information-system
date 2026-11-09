"""order-service: приймає замовлення по HTTP і кладе їх у чергу RabbitMQ."""
import json
import os
import uuid
from datetime import datetime, timezone

import pika
from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from waitress import serve

# Налаштування беруться зі змінних середовища (див. docker-compose.yml).
RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")

EXCHANGE = "coffee"
QUEUE = "orders"
ROUTING_KEY = "orders.created"

MENU = {"espresso": 45, "americano": 50, "cappuccino": 65, "latte": 70, "tea": 35}
MAX_QUANTITY = 10

# Метрики, які забирає Prometheus.
ORDERS_CREATED = Counter(
    "coffee_orders_created_total", "Скільки замовлень прийнято", ["drink"]
)
PUBLISH_FAILED = Counter(
    "coffee_orders_publish_failed_total", "Скільки замовлень не вдалося передати в чергу"
)
for drink_name in MENU:
    ORDERS_CREATED.labels(drink=drink_name)  # щоб метрика була видна ще до першого замовлення

app = Flask(__name__)
app.json.ensure_ascii = False


def publish(message):
    """Передає повідомлення в RabbitMQ і чекає підтвердження від брокера."""
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    parameters = pika.ConnectionParameters(host=RABBIT_HOST, credentials=credentials)

    with pika.BlockingConnection(parameters) as connection:
        channel = connection.channel()
        channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=QUEUE, durable=True)
        channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
        channel.confirm_delivery()
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,  # повідомлення зберігається на диску брокера
            ),
            mandatory=True,
        )


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/menu")
def menu():
    return jsonify(MENU)


@app.post("/orders")
def create_order():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Потрібен JSON із полем drink"), 400

    drink = data.get("drink")
    quantity = data.get("quantity", 1)

    if drink not in MENU:
        return jsonify(error="Такого напою немає в меню", menu=list(MENU)), 400
    if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
        return jsonify(error=f"Кількість має бути від 1 до {MAX_QUANTITY}"), 400

    # Формат повідомлення взято з лекції 9: id, name, time, data.
    message = {
        "id": str(uuid.uuid4()),
        "name": "OrderCreated",
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {
            "drink": drink,
            "quantity": quantity,
            "customer": str(data.get("customer", "гість")),
        },
    }

    try:
        publish(message)
    except (pika.exceptions.AMQPError, OSError) as error:
        PUBLISH_FAILED.inc()
        app.logger.error("Не вдалося передати замовлення: %r", error)
        return jsonify(error="Черга замовлень тимчасово недоступна"), 503

    ORDERS_CREATED.labels(drink=drink).inc()
    return jsonify(order_id=message["id"], status="accepted"), 202


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), content_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=8000)
