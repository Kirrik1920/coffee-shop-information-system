# Кав'ярня: система обробки замовлень

Навчальний проєкт із мікросервісів: два сервіси на Python, RabbitMQ, Prometheus, Grafana та Alertmanager.

## Запуск

Потрібен лише запущений Docker Desktop. Команди виконуй у PowerShell у теці проєкту.

```
docker compose up -d --build
```

Перший запуск займе кілька хвилин. Перевірка стану:

```
docker compose ps
```

## Надіслати тестові замовлення

```
powershell -ExecutionPolicy Bypass -File scripts\send-orders.ps1 -Count 20 -DelayMs 500
```

## Де дивитись результат

| Що | Адреса |
|---|---|
| Grafana, дашборд | http://localhost:3000 |
| RabbitMQ, вхід `coffee` / `coffee_pass` | http://localhost:15672 |
| Prometheus, цілі збору | http://localhost:9090/targets |
| Prometheus, алерти | http://localhost:9090/alerts |
| Alertmanager | http://localhost:9093 |
| Меню | http://localhost:8000/menu |

Виконані замовлення в базі:

```
docker compose exec barista-service python show_orders.py
```

## Демонстрація на захисті

1. Зупини бариста: `docker compose stop barista-service`
2. Надішли замовлення: `powershell -ExecutionPolicy Bypass -File scripts\send-orders.ps1 -Count 15 -DelayMs 200`
3. У Grafana черга зросте, а за хвилину на http://localhost:9090/alerts спрацюють алерти.
4. Запусти бариста: `docker compose start barista-service`
5. Черга спаде, а всі замовлення з'являться в базі.

## Зупинка

```
docker compose down
```

Дані зберігаються в томах Docker. Щоб видалити і їх: `docker compose down -v`.

## Структура

| Тека | Що всередині |
|---|---|
| `docs` | вимоги, архітектура, діаграми, тести, контракт сервісів, трасування, аналіз, заготовка звіту |
| `services/order-service` | приймання замовлень |
| `services/barista-service` | обробка замовлень і база |
| `prometheus` | збір метрик і правила алертів |
| `rabbitmq` | користувачі, virtual host, черги та зв'язки |
| `alertmanager` | обробка алертів |
| `grafana` | джерело даних і готовий дашборд |
| `scripts` | надсилання тестових замовлень |
