from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import Conflict, NotFound
from app.pricing import price_order


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=10_000, strict=True)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: int = Field(gt=0, strict=True)
    address: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    shipping: Literal["pickup", "standard", "express"] = "standard"
    items: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("No repita productos; indique la cantidad en una sola línea")
        return self


class OrderService:
    def __init__(self, database):
        self.database = database

    def create(self, data):
        with self.database.connect() as connection:
            category, lines, pricing = self._price(connection, data)
            cursor = connection.execute(
                """INSERT INTO orders(customer_id, customer_category, address, city, postal_code,
                   shipping, subtotal_cents, discount_cents, shipping_cents, total_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", self._values(data, category, pricing),
            )
            return self._save_lines(connection, cursor.lastrowid, lines)

    def update(self, order_id, data):
        with self.database.connect() as connection:
            self._pending(connection, order_id, "Solo puede editar pedidos pendientes")
            category, lines, pricing = self._price(connection, data)
            connection.execute(
                """UPDATE orders SET customer_id = ?, customer_category = ?, address = ?, city = ?,
                   postal_code = ?, shipping = ?, subtotal_cents = ?, discount_cents = ?,
                   shipping_cents = ?, total_cents = ? WHERE id = ?""",
                (*self._values(data, category, pricing), order_id),
            )
            connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            return self._save_lines(connection, order_id, lines)

    def quote(self, data):
        with self.database.connect() as connection:
            return self._price(connection, data)[2]

    def get(self, order_id):
        with self.database.connect() as connection:
            return self._detail(connection, order_id)

    def list(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY id")]

    def confirm(self, order_id):
        with self.database.connect() as connection:
            order = self._pending(connection, order_id, "El pedido ya no está pendiente")
            items = connection.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            for item in items:
                cursor = connection.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (item["quantity"], item["product_id"], item["quantity"]),
                )
                if cursor.rowcount == 0:
                    raise Conflict(f"Stock insuficiente para producto {item['product_id']}")
            customer = connection.execute("SELECT * FROM customers WHERE id = ?", (order["customer_id"],)).fetchone()
            message = f"Pedido {order_id} confirmado. Total: {order['total_cents']} centavos."
            connection.execute(
                "INSERT INTO notifications(order_id, recipient, message) VALUES (?, ?, ?)",
                (order_id, customer["email"], message),
            )
            connection.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
            return {"id": order_id, "status": "confirmed", "message": message}

    def cancel(self, order_id):
        with self.database.connect() as connection:
            self._pending(connection, order_id, "Solo puede cancelar pedidos pendientes")
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            return {"id": order_id, "status": "cancelled"}

    def delete(self, order_id):
        with self.database.connect() as connection:
            self._pending(connection, order_id, "Solo puede eliminar pedidos pendientes")
            connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def notifications(self, order_id):
        self.get(order_id)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM notifications WHERE order_id = ? ORDER BY id", (order_id,),
            )]

    @staticmethod
    def _find(connection, order_id):
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise NotFound("Pedido no encontrado")
        return order

    def _pending(self, connection, order_id, message):
        order = self._find(connection, order_id)
        if order["status"] != "pending":
            raise Conflict(message)
        return order

    def _detail(self, connection, order_id):
        result = dict(self._find(connection, order_id))
        result["items"] = [dict(row) for row in connection.execute(
            "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY id",
            (order_id,),
        )]
        return result

    @staticmethod
    def _price(connection, data):
        customer = connection.execute("SELECT * FROM customers WHERE id = ?", (data.customer_id,)).fetchone()
        if customer is None:
            raise NotFound("Cliente no encontrado")
        lines = []
        for item in data.items:
            product = connection.execute("SELECT * FROM products WHERE id = ?", (item.product_id,)).fetchone()
            if product is None:
                raise NotFound(f"Producto {item.product_id} no encontrado")
            lines.append((item.product_id, item.quantity, product["price_cents"]))
        subtotal = sum(quantity * price for _, quantity, price in lines)
        return customer["category"], lines, price_order(customer["category"], data.shipping, subtotal)

    @staticmethod
    def _values(data, category, pricing):
        return (data.customer_id, category, data.address, data.city, data.postal_code, data.shipping,
                pricing["subtotal_cents"], pricing["discount_cents"],
                pricing["shipping_cents"], pricing["total_cents"])

    def _save_lines(self, connection, order_id, lines):
        connection.executemany(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )
        return self._detail(connection, order_id)


router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/quote")
def quote_order(data: OrderInput, request: Request):
    return OrderService(request.app.state.database).quote(data)


@router.post("", status_code=201)
def create_order(data: OrderInput, request: Request):
    return OrderService(request.app.state.database).create(data)


@router.get("")
def list_orders(request: Request):
    return OrderService(request.app.state.database).list()


@router.get("/{order_id}")
def get_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).get(order_id)


@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, request: Request):
    return OrderService(request.app.state.database).update(order_id, data)


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, request: Request):
    OrderService(request.app.state.database).delete(order_id)
    return Response(status_code=204)


@router.post("/{order_id}/confirm")
def confirm_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).confirm(order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).cancel(order_id)


@router.get("/{order_id}/notifications")
def list_notifications(order_id: int, request: Request):
    return OrderService(request.app.state.database).notifications(order_id)
