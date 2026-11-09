"""auth-service: сервер авторизації. Перевіряє користувачів і видає токени JWT."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import Flask, Response, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from waitress import serve
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.getenv("AUTH_DB_PATH", "/data/users.db")
JWT_SECRET = os.environ["JWT_SECRET"]
TOKEN_MINUTES = 60
ROLES = ("cashier", "admin")

LOGINS = Counter("coffee_auth_logins_total", "Спроби входу", ["result"])
LOGINS.labels(result="success")
LOGINS.labels(result="failure")

app = Flask(__name__)
app.json.ensure_ascii = False


def connect():
    return sqlite3.connect(DB_PATH)


def create_user(connection, username, password, role):
    connection.execute(
        "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), role),
    )


def init_db():
    """Створює таблицю та початкових користувачів зі змінних середовища."""
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   username TEXT PRIMARY KEY,
                   password_hash TEXT NOT NULL,
                   role TEXT NOT NULL
               )"""
        )
        create_user(connection, os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"], "admin")
        create_user(connection, os.environ["CASHIER_USERNAME"], os.environ["CASHIER_PASSWORD"], "cashier")


def is_text(value):
    """Перевіряє, що значення є рядком без символу NUL, який можна передати в UTF-8."""
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def json_body():
    try:
        data = request.get_json(silent=True)
    except RecursionError:
        return None
    return data if isinstance(data, dict) else None


def token_payload():
    """Повертає вміст токена із заголовка Authorization або None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        return jwt.decode(header[7:], JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.InvalidTokenError:
        return None


def require_token(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        g.user = token_payload()
        if g.user is None:
            return jsonify(error="Потрібен дійсний токен"), 401
        return handler(*args, **kwargs)

    return wrapper


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/login")
def login():
    data = json_body()
    username = data.get("username") if data else None
    password = data.get("password") if data else None
    if not is_text(username) or not is_text(password):
        return jsonify(error="Потрібні поля username і password"), 400

    with connect() as connection:
        row = connection.execute(
            "SELECT password_hash, role FROM users WHERE username = ?", (username,)
        ).fetchone()

    if row is None or not check_password_hash(row[0], password):
        LOGINS.labels(result="failure").inc()
        print(f"Невдалий вхід: {username!r}")
        return jsonify(error="Неправильний логін або пароль"), 401

    LOGINS.labels(result="success").inc()
    print(f"Успішний вхід: {username!r}")
    expires = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_MINUTES)
    token = jwt.encode({"sub": username, "role": row[1], "exp": expires}, JWT_SECRET, algorithm="HS256")
    return jsonify(access_token=token, token_type="Bearer", expires_in=TOKEN_MINUTES * 60)


@app.post("/register")
@require_token
def register():
    if g.user.get("role") != "admin":
        return jsonify(error="Реєструвати користувачів може лише адміністратор"), 403

    data = json_body()
    username = data.get("username") if data else None
    password = data.get("password") if data else None
    role = data.get("role", "cashier") if data else None
    if not is_text(username) or not 3 <= len(username) <= 50:
        return jsonify(error="Логін має містити від 3 до 50 символів"), 400
    if not is_text(password) or not 8 <= len(password) <= 128:
        return jsonify(error="Пароль має містити від 8 до 128 символів"), 400
    if role not in ROLES:
        return jsonify(error="Роль має бути cashier або admin"), 400

    with connect() as connection:
        before = connection.total_changes
        create_user(connection, username, password, role)
        created = connection.total_changes > before
    if not created:
        return jsonify(error="Такий користувач уже існує"), 409
    return jsonify(username=username, role=role), 201


@app.get("/verify")
@require_token
def verify():
    return jsonify(username=g.user["sub"], role=g.user.get("role"))


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), content_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    init_db()
    serve(app, host="0.0.0.0", port=8002)
