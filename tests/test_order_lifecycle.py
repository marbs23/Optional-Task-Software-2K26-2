"""Ciclo de vida de pedidos: edición, estados, validación y recursos inexistentes."""
import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "life.sqlite3")) as session:
        yield session


@pytest.fixture
def setup(client):
    customer = client.post("/customers", json={"name": "Ana", "email": "ana@example.com", "category": "premium"}).json()
    product = client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 5}).json()
    body = {"customer_id": customer["id"], "address": "Calle 1", "city": "Lima", "postal_code": "15001",
            "shipping": "standard", "items": [{"product_id": product["id"], "quantity": 2}]}
    return customer, product, body


def create(client, body):
    response = client.post("/orders", json=body)
    assert response.status_code == 201
    return response.json()


def test_get_and_list_orders(client, setup):
    _, product, body = setup
    first, second = create(client, body), create(client, body)
    detail = client.get(f"/orders/{first['id']}").json()
    assert detail["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 2500}]
    listing = client.get("/orders").json()
    assert [o["id"] for o in listing] == [first["id"], second["id"]]
    assert all("items" not in o for o in listing)


def test_update_recalculates_with_current_prices_and_category(client, setup):
    customer, product, body = setup
    order = create(client, body)
    client.put(f"/products/{product['id']}", json={"name": "Libro", "price_cents": 5000, "stock": 5})
    client.put(f"/customers/{customer['id']}", json={"name": "Ana", "email": "ana@example.com", "category": "business"})
    assert client.get(f"/orders/{order['id']}").json()["total_cents"] == 5500  # sin recalcular
    body["shipping"] = "pickup"
    updated = client.put(f"/orders/{order['id']}", json=body)
    assert updated.status_code == 200
    data = updated.json()
    assert (data["subtotal_cents"], data["discount_cents"], data["shipping_cents"], data["total_cents"]) == (
        10_000, 1_500, 0, 8_500)
    assert data["customer_category"] == "business"
    assert data["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 5000}]


def test_update_replaces_lines(client, setup):
    _, _, body = setup
    other = client.post("/products", json={"name": "Otro", "price_cents": 100, "stock": 1}).json()
    order = create(client, body)
    body["items"] = [{"product_id": other["id"], "quantity": 1}]
    items = client.put(f"/orders/{order['id']}", json=body).json()["items"]
    assert items == [{"product_id": other["id"], "quantity": 1, "unit_price_cents": 100}]


def test_cancel_makes_order_final(client, setup):
    _, product, body = setup
    order = create(client, body)
    cancelled = client.post(f"/orders/{order['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.put(f"/orders/{order['id']}", json=body).status_code == 409
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5


def test_confirmed_order_cannot_be_edited_cancelled_or_deleted(client, setup):
    _, _, body = setup
    order = create(client, body)
    confirmed = client.post(f"/orders/{order['id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert client.put(f"/orders/{order['id']}", json=body).status_code == 409
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"


def test_delete_pending_order_removes_it_and_its_lines(client, setup):
    _, product, body = setup
    order = create(client, body)
    assert client.delete(f"/orders/{order['id']}").status_code == 204
    assert client.get(f"/orders/{order['id']}").status_code == 404
    assert client.delete(f"/products/{product['id']}").status_code == 204  # ya no está referenciado


def test_confirm_records_notification_for_customer(client, setup):
    customer, _, body = setup
    order = create(client, body)
    client.post(f"/orders/{order['id']}/confirm")
    [note] = client.get(f"/orders/{order['id']}/notifications").json()
    assert note["recipient"] == customer["email"]
    assert str(order["id"]) in note["message"] and "5500" in note["message"]


def test_partial_stock_failure_keeps_every_stock_untouched(client, setup):
    _, product, body = setup
    scarce = client.post("/products", json={"name": "Escaso", "price_cents": 100, "stock": 1}).json()
    body["items"].append({"product_id": scarce["id"], "quantity": 2})
    order = create(client, body)
    response = client.post(f"/orders/{order['id']}/confirm")
    assert response.status_code == 409 and str(scarce["id"]) in response.json()["detail"]
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5
    assert client.get(f"/products/{scarce['id']}").json()["stock"] == 1


def test_exact_stock_can_be_confirmed(client, setup):
    _, product, body = setup
    body["items"][0]["quantity"] = 5
    order = create(client, body)
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.get(f"/products/{product['id']}").json()["stock"] == 0


def test_creating_orders_does_not_reserve_stock(client, setup):
    _, product, body = setup
    body["items"][0]["quantity"] = 5
    create(client, body), create(client, body)
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5


def test_missing_resources_return_404(client, setup):
    customer, product, body = setup
    assert client.get("/orders/99").status_code == 404
    assert client.put("/orders/99", json=body).status_code == 404
    assert client.delete("/orders/99").status_code == 404
    assert client.post("/orders/99/confirm").status_code == 404
    assert client.post("/orders/99/cancel").status_code == 404
    assert client.get("/orders/99/notifications").status_code == 404
    for path in ("/orders", "/orders/quote"):
        assert client.post(path, json={**body, "customer_id": 99}).status_code == 404
        assert client.post(path, json={**body, "items": [{"product_id": 99, "quantity": 1}]}).status_code == 404
    assert client.get("/orders").json() == []


def test_failed_creation_leaves_no_partial_order(client, setup):
    _, product, body = setup
    body["items"].append({"product_id": 99, "quantity": 1})
    assert client.post("/orders", json=body).status_code == 404
    assert client.get("/orders").json() == []


@pytest.mark.parametrize("change", [
    {"customer_id": 0},
    {"address": ""},
    {"address": "   "},
    {"city": ""},
    {"postal_code": ""},
    {"shipping": "drone"},
    {"items": []},
    {"items": [{"product_id": 1, "quantity": 0}]},
    {"items": [{"product_id": 1, "quantity": -1}]},
    {"items": [{"product_id": 1, "quantity": 10_001}]},
    {"items": [{"product_id": 1, "quantity": "2"}]},
    {"unknown": 1},
])
def test_invalid_order_is_rejected(client, setup, change):
    _, _, body = setup
    assert client.post("/orders", json={**body, **change}).status_code == 422
    assert client.post("/orders/quote", json={**body, **change}).status_code == 422
    assert client.get("/orders").json() == []


def test_shipping_defaults_to_standard(client, setup):
    _, _, body = setup
    del body["shipping"]
    assert create(client, body)["shipping"] == "standard"
