DISCOUNT_PERCENT = {"regular": 0, "premium": 10, "business": 15}

FREE_SHIPPING_THRESHOLD_CENTS = 10_000
STANDARD_SHIPPING_CENTS = 1_000
EXPRESS_SHIPPING_CENTS = 2_500

# El umbral de envío gratis se aplica al subtotal anterior al descuento.
SHIPPING_COST = {
    "pickup": lambda subtotal: 0,
    "standard": lambda subtotal: 0 if subtotal >= FREE_SHIPPING_THRESHOLD_CENTS else STANDARD_SHIPPING_CENTS,
    "express": lambda subtotal: EXPRESS_SHIPPING_CENTS,
}


def price_order(category, shipping, subtotal):
    discount = subtotal * DISCOUNT_PERCENT.get(category, 0) // 100  # trunca a centavos enteros
    delivery = SHIPPING_COST[shipping](subtotal)
    return {"subtotal_cents": subtotal, "discount_cents": discount,
            "shipping_cents": delivery, "total_cents": subtotal - discount + delivery}
