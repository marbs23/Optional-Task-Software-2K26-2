"""CRUD de productos y clientes, validación y errores de la API."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "crud.sqlite3")) as session:
        yield session


def make_customer(client, email="ana@example.com", category="regular"):
    response = client.post("/customers", json={"name": "Ana", "email": email, "category": category})
    assert response.status_code == 201
    return response.json()


def make_product(client, price=1000, stock=5):
    response = client.post("/products", json={"name": "Libro", "price_cents": price, "stock": stock})
    assert response.status_code == 201
    return response.json()


def order_body(customer_id, product_id, quantity=1):
    return {"customer_id": customer_id, "address": "Calle 1", "city": "Lima", "postal_code": "15001",
            "items": [{"product_id": product_id, "quantity": quantity}]}


# ---- productos ----

def test_product_crud_cycle(client):
    product = make_product(client)
    assert product == {"id": product["id"], "name": "Libro", "price_cents": 1000, "stock": 5}
    assert client.get("/products").json() == [product]
    updated = client.put(f"/products/{product['id']}", json={"name": "Otro", "price_cents": 1, "stock": 0})
    assert updated.status_code == 200 and updated.json()["name"] == "Otro"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 0
    assert client.delete(f"/products/{product['id']}").status_code == 204
    assert client.get(f"/products/{product['id']}").status_code == 404


def test_products_are_listed_by_id(client):
    ids = [make_product(client)["id"] for _ in range(3)]
    assert [p["id"] for p in client.get("/products").json()] == ids


@pytest.mark.parametrize("body", [
    {"name": "", "price_cents": 100, "stock": 1},
    {"name": "P", "price_cents": 0, "stock": 1},
    {"name": "P", "price_cents": -5, "stock": 1},
    {"name": "P", "price_cents": 100, "stock": -1},
    {"name": "P", "price_cents": "100", "stock": 1},
    {"name": "P", "price_cents": 10.5, "stock": 1},
    {"name": "P", "price_cents": 100_000_001, "stock": 1},
    {"name": "P", "price_cents": 100, "stock": 1, "extra": 1},
])
def test_invalid_product_is_rejected(client, body):
    assert client.post("/products", json=body).status_code == 422
    assert client.get("/products").json() == []


def test_missing_product_returns_404(client):
    body = {"name": "P", "price_cents": 100, "stock": 1}
    assert client.get("/products/99").status_code == 404
    assert client.put("/products/99", json=body).status_code == 404
    assert client.delete("/products/99").status_code == 404


def test_product_in_an_order_cannot_be_deleted(client):
    customer, product = make_customer(client), make_product(client)
    assert client.post("/orders", json=order_body(customer["id"], product["id"])).status_code == 201
    assert client.delete(f"/products/{product['id']}").status_code == 409
    assert client.get(f"/products/{product['id']}").status_code == 200


# ---- clientes ----

def test_customer_crud_cycle(client):
    customer = make_customer(client)
    assert customer["category"] == "regular"
    assert client.get("/customers").json() == [customer]
    updated = client.put(f"/customers/{customer['id']}",
                         json={"name": "Ana B", "email": "b@example.com", "category": "business"})
    assert updated.status_code == 200
    assert client.get(f"/customers/{customer['id']}").json() == {
        "id": customer["id"], "name": "Ana B", "email": "b@example.com", "category": "business"}
    assert client.delete(f"/customers/{customer['id']}").status_code == 204
    assert client.get(f"/customers/{customer['id']}").status_code == 404


def test_duplicate_email_is_a_conflict_on_create_and_update(client):
    first, second = make_customer(client, "a@example.com"), make_customer(client, "b@example.com")
    duplicate = client.post("/customers", json={"name": "Z", "email": "a@example.com"})
    assert duplicate.status_code == 409
    clash = client.put(f"/customers/{second['id']}", json={"name": "Z", "email": "a@example.com"})
    assert clash.status_code == 409
    assert client.get(f"/customers/{second['id']}").json()["email"] == "b@example.com"
    assert len(client.get("/customers").json()) == 2 and first["id"] != second["id"]


@pytest.mark.parametrize("body", [
    {"name": "", "email": "a@example.com"},
    {"name": "A", "email": "no-es-correo"},
    {"name": "A", "email": "a@example.com", "category": "vip"},
    {"name": "A", "email": "a@example.com", "extra": 1},
])
def test_invalid_customer_is_rejected(client, body):
    assert client.post("/customers", json=body).status_code == 422


def test_missing_customer_returns_404(client):
    body = {"name": "A", "email": "a@example.com"}
    assert client.get("/customers/99").status_code == 404
    assert client.put("/customers/99", json=body).status_code == 404
    assert client.delete("/customers/99").status_code == 404


def test_customer_with_orders_cannot_be_deleted(client):
    customer, product = make_customer(client), make_product(client)
    client.post("/orders", json=order_body(customer["id"], product["id"]))
    assert client.delete(f"/customers/{customer['id']}").status_code == 409
    assert client.get(f"/customers/{customer['id']}").status_code == 200


# ---- base de datos ----

def test_database_failure_returns_503(tmp_path):
    app = create_app(tmp_path / "x.sqlite3")
    with TestClient(app) as client:
        def broken():
            raise sqlite3.OperationalError("database is locked")
        app.state.database.connect = broken
        response = client.get("/products")
    assert response.status_code == 503
    assert "detail" in response.json()


def test_default_database_path_comes_from_env(tmp_path, monkeypatch):
    path = tmp_path / "env" / "store.sqlite3"
    monkeypatch.setenv("LAB_DB_PATH", str(path))
    with TestClient(create_app()) as client:
        make_product(client)
    assert path.exists()


def test_data_survives_restart(tmp_path):
    path = tmp_path / "persist.sqlite3"
    with TestClient(create_app(path)) as client:
        product = make_product(client)
    with TestClient(create_app(path)) as client:
        assert client.get(f"/products/{product['id']}").json() == product
