# Кав'ярня: інформаційна система обробки замовлень

Навчальний проєкт із дисципліни «Інформаційні системи»: сервіси на Python, PostgreSQL, сервер авторизації, RabbitMQ, Prometheus, Grafana, Loki, Tempo та Alertmanager.

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

Скрипт сам увійде в систему як `cashier`.

```
powershell -ExecutionPolicy Bypass -File scripts\send-orders.ps1 -Count 20 -DelayMs 500
```

## Де дивитись результат

| Що | Адреса | Вхід |
|---|---|---|
| Grafana, дашборд | http://localhost:3000 | `admin` / `coffee_grafana` |
| RabbitMQ | http://localhost:15672 | `coffee` / `coffee_pass` |
| Prometheus, цілі збору | http://localhost:9090/targets | без пароля |
| Prometheus, алерти | http://localhost:9090/alerts | без пароля |
| Alertmanager | http://localhost:9093 | без пароля |

Паролі навчальні та призначені лише для локального запуску.

Дашборд показує замовлення, чергу, роботу PostgreSQL, логи й трейси. Детальніше логи та трейси можна дослідити в Grafana: **Explore**, далі джерело **Loki** або **Tempo**.

- Логи: `{service="barista-service"}`
- Трейси: `{resource.service.name="order-service"}`

Виконані замовлення в базі:

```
docker compose exec barista-service python show_orders.py
```

## Авторизація вручну

Отримати токен:

```
$t = (Invoke-RestMethod -Method Post -Uri http://localhost:8002/login -ContentType 'application/json' -Body '{"username":"cashier","password":"cashier_pass"}').access_token
```

Зробити замовлення:

```
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders -Headers @{Authorization="Bearer $t"} -ContentType 'application/json' -Body '{"drink":"latte","quantity":1}'
```

## Демонстрація на захисті

1. Спробуй замовити без токена: відповідь 401.
2. Зупини бариста: `docker compose stop barista-service`
3. Надішли замовлення: `powershell -ExecutionPolicy Bypass -File scripts\send-orders.ps1 -Count 15 -DelayMs 200`
4. У Grafana черга зросте, а за хвилину на http://localhost:9090/alerts спрацюють алерти.
5. Запусти бариста: `docker compose start barista-service`
6. Черга спаде, замовлення з'являться в PostgreSQL, логах і трейсах.

## Зупинка

```
docker compose down
```

Дані зберігаються в томах Docker. Щоб видалити і їх: `docker compose down -v`.

## Структура

| Тека | Що всередині |
|---|---|
| `docs` | вимоги, архітектура, діаграми, тести, контракт сервісів, трасування, аналіз, звіт |
| `services/auth-service` | сервер авторизації |
| `services/order-service` | приймання замовлень |
| `services/barista-service` | обробка замовлень і запис у PostgreSQL |
| `rabbitmq` | користувачі, virtual host, черги та зв'язки |
| `prometheus` | збір метрик і правила алертів |
| `alertmanager` | обробка алертів |
| `loki` | збір і зберігання логів |
| `tempo` | зберігання трейсів |
| `grafana` | джерела даних і готовий дашборд |
| `scripts` | надсилання тестових замовлень |
