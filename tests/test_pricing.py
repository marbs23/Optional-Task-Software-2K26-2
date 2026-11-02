"""Reglas de precio: descuento, envío y total, a través de OrderService y de la API."""
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.orders import OrderInput, OrderService
from main import create_app


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "pricing.sqlite3")
    db.initialize()
    return db


def seed(database, category, unit_price):
    with database.connect() as c:
        customer = c.execute(
            "INSERT INTO customers(name, email, category) VALUES ('X', 'x@example.com', ?)", (category,)
        ).lastrowid
        product = c.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES ('P', ?, 100)", (unit_price,)
        ).lastrowid
    return customer, product


def order_input(customer, product, shipping, quantity=1):
    return OrderInput(customer_id=customer, address="Calle 1", city="Lima", postal_code="1",
                      shipping=shipping, items=[{"product_id": product, "quantity": quantity}])


# (categoría, precio unitario, envío, subtotal, descuento, costo de envío, total)
CASES = [
    ("regular", 5000, "standard", 5000, 0, 1000, 6000),
    ("premium", 5000, "standard", 5000, 500, 1000, 5500),
    ("business", 5000, "standard", 5000, 750, 1000, 5250),
    ("premium", 9999, "standard", 9999, 999, 1000, 10000),   # el descuento se trunca
    ("regular", 9999, "standard", 9999, 0, 1000, 10999),     # justo bajo el umbral: cobra envío
    ("regular", 10000, "standard", 10000, 0, 0, 10000),      # en el umbral: envío gratis
    ("premium", 10000, "standard", 10000, 1000, 0, 9000),    # umbral sobre subtotal sin descuento
    ("regular", 5000, "pickup", 5000, 0, 0, 5000),
    ("regular", 5000, "express", 5000, 0, 2500, 7500),
    ("regular", 20000, "express", 20000, 0, 2500, 22500),    # express nunca es gratis
    ("business", 1, "pickup", 1, 0, 0, 1),                   # 15 % de 1 centavo se trunca a 0
]


@pytest.mark.parametrize("category,price,shipping,subtotal,discount,delivery,total", CASES)
def test_quote_applies_discount_and_shipping(database, category, price, shipping, subtotal, discount, delivery, total):
    customer, product = seed(database, category, price)
    quote = OrderService(database).quote(order_input(customer, product, shipping))
    assert quote == {"subtotal_cents": subtotal, "discount_cents": discount,
                     "shipping_cents": delivery, "total_cents": total}


@pytest.mark.parametrize("category,price,shipping,subtotal,discount,delivery,total", CASES)
def test_saved_order_matches_quote(database, category, price, shipping, subtotal, discount, delivery, total):
    customer, product = seed(database, category, price)
    service = OrderService(database)
    order = service.create(order_input(customer, product, shipping))
    assert (order["subtotal_cents"], order["discount_cents"], order["shipping_cents"], order["total_cents"]) == (
        subtotal, discount, delivery, total)
    assert order["customer_category"] == category
    assert order["status"] == "pending"
    assert order["items"] == [{"product_id": product, "quantity": 1, "unit_price_cents": price}]


def test_quote_uses_quantity_in_subtotal(database):
    customer, product = seed(database, "regular", 2500)
    quote = OrderService(database).quote(order_input(customer, product, "pickup", quantity=4))
    assert quote["subtotal_cents"] == 10_000


def test_quote_does_not_persist_or_touch_stock(tmp_path):
    with TestClient(create_app(tmp_path / "q.sqlite3")) as client:
        customer = client.post("/customers", json={"name": "A", "email": "a@example.com"}).json()
        product = client.post("/products", json={"name": "P", "price_cents": 100, "stock": 3}).json()
        body = {"customer_id": customer["id"], "address": "x", "city": "y", "postal_code": "1",
                "items": [{"product_id": product["id"], "quantity": 2}]}
        assert client.post("/orders/quote", json=body).status_code == 200
        assert client.get("/orders").json() == []
        assert client.get(f"/products/{product['id']}").json()["stock"] == 3
