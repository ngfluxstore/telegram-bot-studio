
"""Commerce and order management for NGFLUX STORE."""

import secrets
from decimal import Decimal, InvalidOperation


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
                raise ValueError("Please start the bot before placing an order.")

            unit_price = Decimal(str(product["price"]))
            total_amount = unit_price * quantity

            order_number = (
                "NGF-" + secrets.token_hex(5).upper()
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
                    currency
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
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
            )

            return dict(order)
