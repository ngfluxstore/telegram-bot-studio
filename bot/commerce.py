
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
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pending')
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
    product["delivery_mode"],
    product["delivery_content"],
)
