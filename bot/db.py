
"""PostgreSQL access layer for NGFLUX STORE."""

import json
import logging
from decimal import Decimal

import asyncpg


logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 10.0


# --- Existing database tables -----------------------------------------------

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

UPSERT_USER = """
INSERT INTO users (telegram_id, username, first_name)
VALUES ($1, $2, $3)
ON CONFLICT (telegram_id) DO UPDATE
    SET username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        last_seen = now()
RETURNING (xmax = 0) AS is_new;
"""

CREATE_COMMANDS_TABLE = """
CREATE TABLE IF NOT EXISTS commands (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    reply_type TEXT NOT NULL DEFAULT 'text',
    reply_text TEXT NOT NULL DEFAULT '',
    media_url TEXT NOT NULL DEFAULT '',
    keyboard JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    show_in_menu BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_MENU_BUTTONS_TABLE = """
CREATE TABLE IF NOT EXISTS menu_buttons (
    id SERIAL PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    command_name TEXT NOT NULL,
    row_index INTEGER NOT NULL DEFAULT 0 CHECK (row_index >= 0),
    sort_order INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_AUDIT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx
    ON audit_log (created_at DESC);
"""


# --- NGFLUX STORE marketplace tables ---------------------------------------

CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NOT NULL,
    total_amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'awaiting_payment',
    delivery_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_PAYMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    payment_method TEXT NOT NULL,
    transaction_reference TEXT NOT NULL,
    proof_file_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


COMMAND_COLUMNS = (
    "id, name, description, reply_type, reply_text, media_url, "
    "keyboard, enabled, show_in_menu, created_at, updated_at"
)


# --- Database connection -----------------------------------------------------

async def create_pool(dsn: str) -> asyncpg.Pool:
    """Connect to PostgreSQL and initialize all database tables."""

    pool = await asyncpg.create_pool(
        dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT,
    )

    try:
        async with pool.acquire() as conn:
            await conn.execute(CREATE_USERS_TABLE)
            await conn.execute(CREATE_COMMANDS_TABLE)
            await conn.execute(CREATE_MENU_BUTTONS_TABLE)
            await conn.execute(CREATE_AUDIT_LOG_TABLE)

            await conn.execute(CREATE_PRODUCTS_TABLE)
            await conn.execute(CREATE_ORDERS_TABLE)
            await conn.execute(CREATE_PAYMENTS_TABLE)

        logger.info("PostgreSQL pool ready. Marketplace schema initialized.")
        return pool

    except Exception:
        await pool.close()
        raise


async def upsert_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> bool:
    is_new = await pool.fetchval(
        UPSERT_USER, telegram_id, username, first_name
    )
    return bool(is_new)


async def count_users(pool: asyncpg.Pool) -> int:
    return int(await pool.fetchval("SELECT count(*) FROM users;"))


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()
    logger.info("PostgreSQL pool closed.")


# --- Dynamic command management ---------------------------------------------

def _command_to_dict(row: asyncpg.Record) -> dict:
    data = dict(row)
    keyboard = data.get("keyboard")

    if isinstance(keyboard, str):
        try:
            data["keyboard"] = json.loads(keyboard)
        except (ValueError, TypeError):
            data["keyboard"] = None

    return data


async def list_commands(
    pool: asyncpg.Pool, *, enabled_only: bool = False
) -> list[dict]:
    query = f"SELECT {COMMAND_COLUMNS} FROM commands"

    if enabled_only:
        query += " WHERE enabled = TRUE"

    query += " ORDER BY name;"
    rows = await pool.fetch(query)

    return [_command_to_dict(row) for row in rows]


async def get_command(
    pool: asyncpg.Pool, command_id: int
) -> dict | None:
    row = await pool.fetchrow(
        f"SELECT {COMMAND_COLUMNS} FROM commands WHERE id = $1;",
        command_id,
    )

    return _command_to_dict(row) if row is not None else None


async def create_command(
    pool: asyncpg.Pool,
    *,
    name: str,
    description: str,
    reply_type: str,
    reply_text: str,
    media_url: str,
    keyboard: list | None,
    enabled: bool,
    show_in_menu: bool,
) -> dict:
    row = await pool.fetchrow(
        f"""
        INSERT INTO commands
            (name, description, reply_type, reply_text, media_url,
             keyboard, enabled, show_in_menu)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING {COMMAND_COLUMNS};
        """,
        name,
        description,
        reply_type,
        reply_text,
        media_url,
        json.dumps(keyboard) if keyboard else None,
        enabled,
        show_in_menu,
    )

    return _command_to_dict(row)


async def update_command(
    pool: asyncpg.Pool,
    command_id: int,
    *,
    name: str,
    description: str,
    reply_type: str,
    reply_text: str,
    media_url: str,
    keyboard: list | None,
    enabled: bool,
    show_in_menu: bool,
) -> dict | None:
    async with pool.acquire() as conn, conn.transaction():
        old_name = await conn.fetchval(
            "SELECT name FROM commands WHERE id = $1 FOR UPDATE;",
            command_id,
        )

        if old_name is None:
            return None

        if old_name != name:
            if keyboard:
                keyboard = [
                    [
                        f"/{name}" if value == f"/{old_name}" else value
                        for value in group
                    ]
                    for group in keyboard
                ]

            await conn.execute(
                "UPDATE menu_buttons SET command_name = $2, updated_at = now() "
                "WHERE command_name = $1;",
                old_name,
                name,
            )

            rows = await conn.fetch(
                "SELECT id, keyboard FROM commands WHERE keyboard IS NOT NULL;"
            )

            for command_row in rows:
                layout = command_row["keyboard"]

                if isinstance(layout, str):
                    layout = json.loads(layout)

                updated_layout = [
                    [
                        f"/{name}" if value == f"/{old_name}" else value
                        for value in row
                    ]
                    for row in layout
                ]

                if updated_layout != layout:
                    await conn.execute(
                        "UPDATE commands SET keyboard = $2, updated_at = now() "
                        "WHERE id = $1;",
                        command_row["id"],
                        json.dumps(updated_layout),
                    )

        row = await conn.fetchrow(
            f"""
            UPDATE commands SET
                name = $2,
                description = $3,
                reply_type = $4,
                reply_text = $5,
                media_url = $6,
                keyboard = $7,
                enabled = $8,
                show_in_menu = $9,
                updated_at = now()
            WHERE id = $1
            RETURNING {COMMAND_COLUMNS};
            """,
            command_id,
            name,
            description,
            reply_type,
            reply_text,
            media_url,
            json.dumps(keyboard) if keyboard else None,
            enabled,
            show_in_menu,
        )

    return _command_to_dict(row) if row is not None else None


async def delete_command(
    pool: asyncpg.Pool, command_id: int
) -> bool:
    result = await pool.execute(
        "DELETE FROM commands WHERE id = $1;", command_id
    )
    return result.endswith("1")


async def command_dependencies(
    pool: asyncpg.Pool, name: str
) -> dict:
    menu_count = int(
        await pool.fetchval(
            "SELECT count(*) FROM menu_buttons WHERE command_name = $1;",
            name,
        )
    )

    response_count = 0
    rows = await pool.fetch(
        "SELECT keyboard FROM commands WHERE keyboard IS NOT NULL;"
    )

    for row in rows:
        layout = row["keyboard"]

        if isinstance(layout, str):
            layout = json.loads(layout)

        response_count += sum(
            value == f"/{name}"
            for group in layout
            for value in group
        )

    return {
        "menu_buttons": menu_count,
        "response_buttons": response_count,
    }


async def update_command_keyboard(
    pool: asyncpg.Pool,
    command_id: int,
    keyboard: list | None,
) -> dict | None:
    row = await pool.fetchrow(
        f"""
        UPDATE commands
        SET keyboard = $2, updated_at = now()
        WHERE id = $1
        RETURNING {COMMAND_COLUMNS};
        """,
        command_id,
        json.dumps(keyboard) if keyboard else None,
    )

    return _command_to_dict(row) if row is not None else None


# --- Main reply-keyboard buttons ---------------------------------------------

async def list_menu_buttons(
    pool: asyncpg.Pool, *, enabled_only: bool = False
) -> list[dict]:
    query = "SELECT * FROM menu_buttons"

    if enabled_only:
        query += " WHERE enabled = TRUE"

    query += " ORDER BY row_index, sort_order, id;"

    return [dict(row) for row in await pool.fetch(query)]


async def get_menu_button(
    pool: asyncpg.Pool, button_id: int
) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM menu_buttons WHERE id = $1;",
        button_id,
    )

    return dict(row) if row is not None else None


async def create_menu_button(
    pool: asyncpg.Pool,
    *,
    label: str,
    command_name: str,
    row_index: int,
    sort_order: int,
    enabled: bool,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO menu_buttons
            (label, command_name, row_index, sort_order, enabled)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *;
        """,
        label,
        command_name,
        row_index,
        sort_order,
        enabled,
    )

    return dict(row)


async def update_menu_button(
    pool: asyncpg.Pool,
    button_id: int,
    *,
    label: str,
    command_name: str,
    row_index: int,
    sort_order: int,
    enabled: bool,
) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE menu_buttons SET
            label = $2,
            command_name = $3,
            row_index = $4,
            sort_order = $5,
            enabled = $6,
            updated_at = now()
        WHERE id = $1
        RETURNING *;
        """,
        button_id,
        label,
        command_name,
        row_index,
        sort_order,
        enabled,
    )

    return dict(row) if row is not None else None


async def reorder_menu_buttons(
    pool: asyncpg.Pool, button_ids: list[int]
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        for position, button_id in enumerate(button_ids):
            await conn.execute(
                "UPDATE menu_buttons SET row_index = $2, sort_order = $3, "
                "updated_at = now() WHERE id = $1;",
                button_id,
                position // 2,
                position % 2,
            )


async def delete_menu_button(
    pool: asyncpg.Pool, button_id: int
) -> bool:
    result = await pool.execute(
        "DELETE FROM menu_buttons WHERE id = $1;",
        button_id,
    )
    return result.endswith("1")


# --- Audit log ---------------------------------------------------------------

async def add_audit_log(
    pool: asyncpg.Pool,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_name: str,
    details: dict | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO audit_log
            (actor, action, entity_type, entity_name, details)
        VALUES ($1, $2, $3, $4, $5);
        """,
        actor,
        action,
        entity_type,
        entity_name,
        json.dumps(details) if details else None,
    )


async def list_audit_log(
    pool: asyncpg.Pool, *, limit: int = 20
) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT $1;",
        limit,
    )

    return [dict(row) for row in rows]


async def clear_audit_log(pool: asyncpg.Pool) -> int:
    result = await pool.execute("DELETE FROM audit_log;")
    return int(result.rsplit(" ", 1)[-1])


# --- NGFLUX STORE product management ----------------------------------------

async def create_product(
    pool: asyncpg.Pool,
    *,
    name: str,
    description: str,
    category: str,
    price,
    currency: str = "USD",
) -> dict:
    """Create a new marketplace product."""

    row = await pool.fetchrow(
        """
        INSERT INTO products
            (name, description, category, price, currency)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *;
        """,
        name,
        description,
        category,
        Decimal(str(price)),
        currency,
    )

    return dict(row)


async def list_products(
    pool: asyncpg.Pool,
    *,
    active_only: bool = True,
    category: str | None = None,
) -> list[dict]:
    """List marketplace products."""

    if category:
        rows = await pool.fetch(
            """
            SELECT * FROM products
            WHERE category = $1
              AND ($2::boolean = FALSE OR active = TRUE)
            ORDER BY created_at DESC;
            """,
            category,
            active_only,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM products
            WHERE ($1::boolean = FALSE OR active = TRUE)
            ORDER BY created_at DESC;
            """,
            active_only,
        )

    return [dict(row) for row in rows]


async def get_product(
    pool: asyncpg.Pool,
    product_id: int,
) -> dict | None:
    """Get one product by its ID."""

    row = await pool.fetchrow(
        "SELECT * FROM products WHERE id = $1;",
        product_id,
    )

    return dict(row) if row is not None else None


async def update_product(
    pool: asyncpg.Pool,
    product_id: int,
    *,
    name: str,
    description: str,
    category: str,
    price,
    currency: str,
    active: bool,
) -> dict | None:
    """Update an existing product."""

    row = await pool.fetchrow(
        """
        UPDATE products SET
            name = $2,
            description = $3,
            category = $4,
            price = $5,
            currency = $6,
            active = $7,
            updated_at = now()
        WHERE id = $1
        RETURNING *;
        """,
        product_id,
        name,
        description,
        category,
        Decimal(str(price)),
        currency,
        active,
    )

    return dict(row) if row is not None else None


async def deactivate_product(
    pool: asyncpg.Pool,
    product_id: int,
) -> bool:
    """Deactivate a product without deleting its order history."""

    result = await pool.execute(
        """
        UPDATE products
        SET active = FALSE, updated_at = now()
        WHERE id = $1 AND active = TRUE;
        """,
        product_id,
    )

    return result.endswith("1")
