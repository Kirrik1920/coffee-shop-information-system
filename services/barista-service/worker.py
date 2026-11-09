"""barista-service: бере замовлення з черги, «готує» їх і зберігає в PostgreSQL."""
import json
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone

import pika
import psycopg
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, start_http_server

RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")
RABBIT_VHOST = os.getenv("RABBIT_VHOST", "/")
DATABASE_URL = os.environ["DATABASE_URL"]
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:4318/v1/traces")
PREP_MIN = float(os.getenv("PREP_MIN_SECONDS", "2"))
PREP_MAX = float(os.getenv("PREP_MAX_SECONDS", "4"))
MAX_QUANTITY = 10

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
DB_ERRORS = Counter("coffee_db_errors_total", "Скільки разів не вдалося виконати запит до БД")
PROCESSING_TIME = Histogram(
    "coffee_order_processing_seconds",
    "Час приготування одного замовлення",
    buckets=(1, 2, 3, 4, 5, 7, 10),
)
DB_INSERT_TIME = Histogram(
    "coffee_db_insert_seconds",
    "Час запису замовлення в PostgreSQL",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1),
)

# Трейси надсилаються в Grafana Tempo.
provider = TracerProvider(resource=Resource.create({"service.name": "barista-service"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=TEMPO_URL)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("barista-service")

db = None


def ensure_db():
    """Підключається до PostgreSQL, якщо з'єднання ще немає або воно втрачене."""
    global db
    if db is not None and not db.closed:
        return db
    db = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5)
    db.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id TEXT PRIMARY KEY,
               drink TEXT NOT NULL,
               quantity INTEGER NOT NULL,
               customer TEXT,
               created_at TEXT,
               done_at TEXT NOT NULL
           )"""
    )
    return db


def already_done(order_id):
    """Перевіряє, чи замовлення вже виконано (захист від повторної доставки)."""
    return ensure_db().execute("SELECT 1 FROM orders WHERE id = %s", (order_id,)).fetchone() is not None


def save_order(order_id, order, created_at):
    with tracer.start_as_current_span("postgres insert", kind=trace.SpanKind.CLIENT), DB_INSERT_TIME.time():
        ensure_db().execute(
            "INSERT INTO orders VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (
                order_id,
                order["drink"],
                order["quantity"],
                order.get("customer"),
                created_at,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def is_text(value):
    """Перевіряє, що значення є рядком без символу NUL, який можна зберегти в UTF-8."""
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_message(body):
    """Перевіряє повідомлення за контрактом. Якщо воно неправильне, викликає ValueError."""
    try:
        message = json.loads(body)
    except RecursionError as error:
        raise ValueError("JSON має надто глибоку вкладеність") from error
    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
        raise ValueError("повідомлення має містити об'єкт data")

    order_id = message.get("id")
    created_at = message.get("time")
    order = message["data"]

    if not is_text(order_id) or not order_id:
        raise ValueError("поле id має бути непорожнім рядком")
    if not is_text(order.get("drink")) or not order["drink"]:
        raise ValueError("поле drink має бути непорожнім рядком")
    if type(order.get("quantity")) is not int or not 1 <= order["quantity"] <= MAX_QUANTITY:
        raise ValueError(f"поле quantity має бути цілим числом від 1 до {MAX_QUANTITY}")
    if order.get("customer") is not None and not is_text(order["customer"]):
        raise ValueError("поле customer має бути рядком")
    if created_at is not None and not is_text(created_at):
        raise ValueError("поле time має бути рядком")

    return order_id, order, created_at


def handle_order(channel, method, properties, body):
    """Обробляє одне повідомлення з черги."""
    global db
    try:
        order_id, order, created_at = parse_message(body)
    except ValueError as error:
        # Зіпсоване повідомлення не повертаємо в чергу orders:
        # RabbitMQ перекладе його в окрему чергу orders.invalid для аналізу.
        ORDERS_FAILED.inc()
        print(f"Відхилено повідомлення: {error!r}")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Продовжуємо трейс, який почав order-service.
    context = extract(properties.headers or {})
    with tracer.start_as_current_span("process order", context=context, kind=trace.SpanKind.CONSUMER) as span:
        span.set_attribute("order.id", order_id)
        span.set_attribute("order.drink", order["drink"])
        trace_id = format(span.get_span_context().trace_id, "032x")
        try:
            if already_done(order_id):
                ORDERS_DUPLICATE.inc()
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            with tracer.start_as_current_span("prepare drink"), PROCESSING_TIME.time():
                time.sleep(random.uniform(PREP_MIN, PREP_MAX))  # імітація приготування

            save_order(order_id, order, created_at)
        except psycopg.Error as error:
            # База недоступна: повертаємо замовлення в чергу, щоб виконати його пізніше.
            DB_ERRORS.inc()
            print(f"Помилка PostgreSQL, замовлення повернуто в чергу: {error!r} trace_id={trace_id}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            if db is not None and not db.closed:
                db.close()
            db = None
            time.sleep(3)
            return

        ORDERS_PROCESSED.labels(drink=order["drink"]).inc()

        # Підтвердження надсилаємо лише після збереження: при збої замовлення не загубиться.
        channel.basic_ack(delivery_tag=method.delivery_tag)
        print(f"Готово: {order['drink']} x{order['quantity']} ({order_id}) trace_id={trace_id}")


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
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    start_http_server(8001)

    while True:
        try:
            ensure_db()
            consume()
        except psycopg.Error as error:
            print(f"PostgreSQL недоступний, нова спроба через 3 секунди: {type(error).__name__}")
            time.sleep(3)
        except (pika.exceptions.AMQPConnectionError, OSError):
            # OSError охоплює випадок, коли адресу rabbitmq неможливо знайти в мережі Docker.
            print("RabbitMQ недоступний, нова спроба через 3 секунди")
            time.sleep(3)


if __name__ == "__main__":
    main()
