
"""Commerce and order management for NGFLUX STORE."""

import secrets
from decimal import Decimal, InvalidOperation


async def create_order(pool, *, telegram_id, product_id, quantity=1):
    """Create an order using the current product price and delivery settings."""

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Invalid quantity.")

    if quantity < 1:
        raise ValueError("Quantity must be at least 1.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            product = await conn.fetchrow(
                """
                SELECT
                    id,
                    name,
                    price,
                    currency,
                    active,
                    delivery_mode,
                    delivery_content
                FROM products
                WHERE id = $1
                FOR SHARE;
                """,
                product_id,
            )

            if not product or not product["active"]:
                raise ValueError("This product is unavailable.")

            user_exists = await conn.fetchval(
                "SELECT 1 FROM users WHERE telegram_id = $1;",
                telegram_id,
            )

            if not user_exists:
                raise ValueError(
                    "Please start the bot before placing an order."
                )

            unit_price = Decimal(str(product["price"]))
            total_amount = unit_price * quantity

            order_number = "NGF-" + secrets.token_hex(5).upper()

            delivery_mode = product["delivery_mode"] or "manual"
            delivery_content = product["delivery_content"] or ""

            if delivery_mode == "automatic" and not delivery_content.strip():
                raise ValueError(
                    "Automatic delivery is not configured for this product."
                )

            order = await conn.fetchrow(
                """
                INSERT INTO orders (
                    order_number,
                    telegram_id,
                    product_id,
                    product_name,
                    quantity,
                    unit_price,
                    total_amount,
                    currency,
                    delivery_mode,
                    delivery_content,
                    delivery_status
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending'
                )
                RETURNING
                    id,
                    order_number,
                    telegram_id,
                    product_id,
                    product_name,
                    quantity,
                    unit_price,
                    total_amount,
                    currency,
                    status,
                    delivery_mode,
                    delivery_content,
                    delivery_status,
                    created_at;
                """,
                order_number,
                telegram_id,
                product["id"],
                product["name"],
                quantity,
                unit_price,
                total_amount,
                product["currency"],
                delivery_mode,
                delivery_content,
            )

            return dict(order)
