
"""NGFLUX STORE order and payment operations."""

import secrets
import string
import json
from decimal import Decimal, InvalidOperation

import asyncpg


ORDER_STATUSES = {
    "awaiting_payment",
    "payment_submitted",
    "paid",
    "processing",
    "completed",
    "cancelled",
    "rejected",
}


def money(value):
    try:
        result = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid price.")

    if not result.is_finite() or result < 0:
        raise ValueError("Invalid price.")

    return result


def make_order_number():
    chars = string.ascii_uppercase + string.digits
    return "NGF-" + "".join(
        secrets.choice(chars) for _ in range(8)
    )


async def create_order(
    pool,
    telegram_id,
    product_id,
    quantity=1,
    selected_option=None
):
    if not isinstance(quantity, int) or not 1 <= quantity <= 100:
        raise ValueError("Quantity must be between 1 and 100.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            product = await conn.fetchrow(
                """
                SELECT * FROM products
                WHERE id=$1 AND active=TRUE
                FOR SHARE
                """,
                product_id
            )

            if not product:
                raise ValueError("Product is unavailable.")

            unit_price = money(product["price"])
            product_name = product["name"]

            options = product["pricing_options"] or []

            if isinstance(options, str):
                options = json.loads(options)

            if selected_option:
                option = next(
                    (
                        item for item in options
                        if item.get("label", "").casefold()
                        == selected_option.strip().casefold()
                    ),
                    None
                )

                if not option:
                    raise ValueError("Pricing option unavailable.")

                unit_price = money(option["price"])
                product_name += " - " + option["label"]

            total = unit_price * quantity

            order = None

            for attempt in range(5):
                order_number = make_order_number()

                try:
                    order = await conn.fetchrow(
                        """
                        INSERT INTO orders
                        (
                            order_number,
                            telegram_id,
                            product_id,
                            product_name,
                            quantity,
                            unit_price,
                            total_amount,
                            currency,
                            status
                        )
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,
                                'awaiting_payment')
                        RETURNING *
                        """,
                        order_number,
                        telegram_id,
                        product_id,
                        product_name,
                        quantity,
                        unit_price,
                        total,
                        product["currency"]
                    )
                    break

                except asyncpg.UniqueViolationError:
                    if attempt == 4:
                        raise RuntimeError(
                            "Could not generate a unique order number."
                        )

            return dict(order)


async def customer_orders(pool, telegram_id):
    rows = await pool.fetch(
        """
        SELECT *
        FROM orders
        WHERE telegram_id=$1
        ORDER BY created_at DESC
        LIMIT 30
        """,
        telegram_id
    )

    return [dict(row) for row in rows]


async def get_customer_order(pool, order_number, telegram_id):
    row = await pool.fetchrow(
        """
        SELECT *
        FROM orders
        WHERE order_number=$1 AND telegram_id=$2
        """,
        order_number.strip().upper(),
        telegram_id
    )

    return dict(row) if row else None


async def submit_payment(
    pool,
    order_number,
    telegram_id,
    payment_method,
    reference,
    proof_file_id=None
):
    if not payment_method or not payment_method.strip():
        raise ValueError("Payment method is required.")

    if not reference or not reference.strip():
        raise ValueError("Transaction reference is required.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                """
                SELECT *
                FROM orders
                WHERE order_number=$1 AND telegram_id=$2
                FOR UPDATE
                """,
                order_number.strip().upper(),
                telegram_id
            )

            if not order:
                raise ValueError("Order not found.")

            if order["status"] != "awaiting_payment":
                raise ValueError(
                    "This order cannot accept another payment submission."
                )

            existing = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM payments
                    WHERE order_id=$1 AND status='pending'
                )
                """,
                order["id"]
            )

            if existing:
                raise ValueError(
                    "A payment for this order is already awaiting review."
                )

            payment = await conn.fetchrow(
                """
                INSERT INTO payments
                (
                    order_id,
                    payment_method,
                    transaction_reference,
                    proof_file_id,
                    status
                )
                VALUES ($1,$2,$3,$4,'pending')
                RETURNING *
                """,
                order["id"],
                payment_method.strip(),
                reference.strip(),
                proof_file_id
            )

            await conn.execute(
                """
                UPDATE orders
                SET status='payment_submitted',
                    updated_at=now()
                WHERE id=$1
                """,
                order["id"]
            )

    return dict(payment)


async def pending_payments(pool):
    rows = await pool.fetch(
        """
        SELECT
            p.*,
            o.order_number,
            o.telegram_id,
            o.product_name,
            o.total_amount,
            o.currency
        FROM payments p
        JOIN orders o ON o.id=p.order_id
        WHERE p.status='pending'
        ORDER BY p.created_at ASC
        """
    )

    return [dict(row) for row in rows]


async def review_payment(pool, payment_id, approved, admin):
    async with pool.acquire() as conn:
        async with conn.transaction():
            payment = await conn.fetchrow(
                """
                SELECT
                    p.*,
                    o.id AS linked_order_id,
                    o.telegram_id,
                    o.order_number
                FROM payments p
                JOIN orders o ON o.id=p.order_id
                WHERE p.id=$1
                FOR UPDATE OF p, o
                """,
                payment_id
            )

            if not payment:
                raise ValueError("Payment not found.")

            if payment["status"] != "pending":
                raise ValueError("Payment already reviewed.")

            payment_status = (
                "approved" if approved else "rejected"
            )

            order_status = (
                "paid" if approved else "awaiting_payment"
            )

            await conn.execute(
                """
                UPDATE payments
                SET status=$2,
                    reviewed_by=$3,
                    reviewed_at=now()
                WHERE id=$1
                """,
                payment_id,
                payment_status,
                str(admin)
            )

            await conn.execute(
                """
                UPDATE orders
                SET status=$2,
                    updated_at=now()
                WHERE id=$1
                """,
                payment["linked_order_id"],
                order_status
            )

            updated = await conn.fetchrow(
                """
                SELECT
                    p.*,
                    o.order_number,
                    o.telegram_id,
                    o.product_name,
                    o.total_amount,
                    o.currency
                FROM payments p
                JOIN orders o ON o.id=p.order_id
                WHERE p.id=$1
                """,
                payment_id
            )

    return dict(updated)


async def update_order(
    pool,
    order_number,
    status,
    delivery_note=""
):
    if status not in ORDER_STATUSES:
        raise ValueError("Invalid order status.")

    row = await pool.fetchrow(
        """
        UPDATE orders
        SET status=$2,
            delivery_note=$3,
            updated_at=now()
        WHERE order_number=$1
        RETURNING *
        """,
        order_number.strip().upper(),
        status,
        delivery_note
    )

    if not row:
        raise ValueError("Order not found.")

    return dict(row)


async def list_orders(pool, limit=50):
    rows = await pool.fetch(
        """
        SELECT
            o.*,
            u.username,
            u.first_name
        FROM orders o
        LEFT JOIN users u
            ON u.telegram_id=o.telegram_id
        ORDER BY o.created_at DESC
        LIMIT $1
        """,
        max(1, min(int(limit), 200))
    )

    return [dict(row) for row in rows]
