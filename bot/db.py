
"""PostgreSQL access layer for NGFLUX STORE."""

import json
import logging
from decimal import Decimal

import asyncpg

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 10.0

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

# Safe migrations: existing products and orders are retained.
MIGRATE_PRODUCTS_TABLE = """
ALTER TABLE products
ADD COLUMN IF NOT EXISTS country TEXT NOT NULL DEFAULT '';

ALTER TABLE products
ADD COLUMN IF NOT EXISTS features TEXT NOT NULL DEFAULT '';

ALTER TABLE products
ADD COLUMN IF NOT EXISTS compatibility TEXT NOT NULL DEFAULT '';

ALTER TABLE products
ADD COLUMN IF NOT EXISTS limits TEXT NOT NULL DEFAULT '';

ALTER TABLE products
ADD COLUMN IF NOT EXISTS pricing_options JSONB NOT NULL DEFAULT '[]'::jsonb;
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


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Connect to PostgreSQL and initialize the database."""

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
            await conn.execute(MIGRATE_PRODUCTS_TABLE)
            await conn.execute(CREATE_ORDERS_TABLE)
            await conn.execute(CREATE_PAYMENTS_TABLE)

        logger.info("PostgreSQL pool ready. Marketplace schema initialized.")
        return pool

    except Exception:
        await pool.close()
        raise


async def upsert_user(pool, telegram_id, username, first_name) -> bool:
    result = await pool.fetchval(
        UPSERT_USER, telegram_id, username, first_name
    )
    return bool(result)


async def count_users(pool) -> int:
    return int(await pool.fetchval("SELECT count(*) FROM users;"))


async def close_pool(pool) -> None:
    await pool.close()
    logger.info("PostgreSQL pool closed.")


# --- Dynamic command management ---------------------------------------------

def _command_to_dict(row):
    data = dict(row)
    keyboard = data.get("keyboard")
    if isinstance(keyboard, str):
        try:
            data["keyboard"] = json.loads(keyboard)
        except (ValueError, TypeError):
            data["keyboard"] = None
    return data


async def list_commands(pool, *, enabled_only=False):
    query = f"SELECT {COMMAND_COLUMNS} FROM commands"
    if enabled_only:
        query += " WHERE enabled = TRUE"
    query += " ORDER BY name;"
    return [_command_to_dict(row) for row in await pool.fetch(query)]


async def get_command(pool, command_id):
    row = await pool.fetchrow(
        f"SELECT {COMMAND_COLUMNS} FROM commands WHERE id = $1;",
        command_id,
    )
    return _command_to_dict(row) if row else None


async def create_command(
    pool, *, name, description, reply_type, reply_text,
    media_url, keyboard, enabled, show_in_menu
):
    row = await pool.fetchrow(
        f"""
        INSERT INTO commands
        (name, description, reply_type, reply_text, media_url,
         keyboard, enabled, show_in_menu)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING {COMMAND_COLUMNS};
        """,
        name, description, reply_type, reply_text, media_url,
        json.dumps(keyboard) if keyboard else None,
        enabled, show_in_menu,
    )
    return _command_to_dict(row)


async def update_command(
    pool, command_id, *, name, description, reply_type, reply_text,
    media_url, keyboard, enabled, show_in_menu
):
    async with pool.acquire() as conn, conn.transaction():
        old_name = await conn.fetchval(
            "SELECT name FROM commands WHERE id=$1 FOR UPDATE;",
            command_id,
        )
        if old_name is None:
            return None

        if old_name != name:
            if keyboard:
                keyboard = [
                    [
                        f"/{name}" if v == f"/{old_name}" else v
                        for v in group
                    ]
                    for group in keyboard
                ]

            await conn.execute(
                "UPDATE menu_buttons SET command_name=$2, updated_at=now() "
                "WHERE command_name=$1;",
                old_name, name,
            )

            rows = await conn.fetch(
                "SELECT id, keyboard FROM commands WHERE keyboard IS NOT NULL;"
            )
            for item in rows:
                layout = item["keyboard"]
                if isinstance(layout, str):
                    layout = json.loads(layout)
                updated = [
                    [
                        f"/{name}" if v == f"/{old_name}" else v
                        for v in group
                    ]
                    for group in layout
                ]
                if updated != layout:
                    await conn.execute(
                        "UPDATE commands SET keyboard=$2, updated_at=now() "
                        "WHERE id=$1;",
                        item["id"], json.dumps(updated),
                    )

        row = await conn.fetchrow(
            f"""
            UPDATE commands SET
                name=$2, description=$3, reply_type=$4, reply_text=$5,
                media_url=$6, keyboard=$7, enabled=$8, show_in_menu=$9,
                updated_at=now()
            WHERE id=$1
            RETURNING {COMMAND_COLUMNS};
            """,
            command_id, name, description, reply_type, reply_text,
            media_url, json.dumps(keyboard) if keyboard else None,
            enabled, show_in_menu,
        )
    return _command_to_dict(row) if row else None


async def delete_command(pool, command_id):
    result = await pool.execute(
        "DELETE FROM commands WHERE id=$1;", command_id
    )
    return result.endswith("1")


async def command_dependencies(pool, name):
    menu_count = int(await pool.fetchval(
        "SELECT count(*) FROM menu_buttons WHERE command_name=$1;", name
    ))
    response_count = 0
    rows = await pool.fetch(
        "SELECT keyboard FROM commands WHERE keyboard IS NOT NULL;"
    )
    for row in rows:
        layout = row["keyboard"]
        if isinstance(layout, str):
            layout = json.loads(layout)
        response_count += sum(
            value == f"/{name}" for group in layout for value in group
        )
    return {"menu_buttons": menu_count, "response_buttons": response_count}


async def update_command_keyboard(pool, command_id, keyboard):
    row = await pool.fetchrow(
        f"""
        UPDATE commands SET keyboard=$2, updated_at=now()
        WHERE id=$1 RETURNING {COMMAND_COLUMNS};
        """,
        command_id, json.dumps(keyboard) if keyboard else None,
    )
    return _command_to_dict(row) if row else None


# --- Main reply-keyboard buttons ---------------------------------------------

async def list_menu_buttons(pool, *, enabled_only=False):
    query = "SELECT * FROM menu_buttons"
    if enabled_only:
        query += " WHERE enabled=TRUE"
    query += " ORDER BY row_index, sort_order, id;"
    return [dict(row) for row in await pool.fetch(query)]


async def get_menu_button(pool, button_id):
    row = await pool.fetchrow(
        "SELECT * FROM menu_buttons WHERE id=$1;", button_id
    )
    return dict(row) if row else None


async def create_menu_button(
    pool, *, label, command_name, row_index, sort_order, enabled
):
    row = await pool.fetchrow(
        """
        INSERT INTO menu_buttons
        (label, command_name, row_index, sort_order, enabled)
        VALUES ($1,$2,$3,$4,$5) RETURNING *;
        """,
        label, command_name, row_index, sort_order, enabled,
    )
    return dict(row)


async def update_menu_button(
    pool, button_id, *, label, command_name, row_index, sort_order, enabled
):
    row = await pool.fetchrow(
        """
        UPDATE menu_buttons SET label=$2, command_name=$3,
        row_index=$4, sort_order=$5, enabled=$6, updated_at=now()
        WHERE id=$1 RETURNING *;
        """,
        button_id, label, command_name, row_index, sort_order, enabled,
    )
    return dict(row) if row else None


async def reorder_menu_buttons(pool, button_ids):
    async with pool.acquire() as conn, conn.transaction():
        for position, button_id in enumerate(button_ids):
            await conn.execute(
                "UPDATE menu_buttons SET row_index=$2, sort_order=$3, "
                "updated_at=now() WHERE id=$1;",
                button_id, position // 2, position % 2,
            )


async def delete_menu_button(pool, button_id):
    result = await pool.execute(
        "DELETE FROM menu_buttons WHERE id=$1;", button_id
    )
    return result.endswith("1")


# --- Audit log ---------------------------------------------------------------

async def add_audit_log(
    pool, *, actor, action, entity_type, entity_name, details=None
):
    await pool.execute(
        """
        INSERT INTO audit_log
        (actor, action, entity_type, entity_name, details)
        VALUES ($1,$2,$3,$4,$5);
        """,
        actor, action, entity_type, entity_name,
        json.dumps(details) if details else None,
    )


async def list_audit_log(pool, *, limit=20):
    rows = await pool.fetch(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT $1;",
        limit,
    )
    return [dict(row) for row in rows]


async def clear_audit_log(pool):
    result = await pool.execute("DELETE FROM audit_log;")
    return int(result.rsplit(" ", 1)[-1])


# --- Product management ------------------------------------------------------

def _product_to_dict(row):
    data = dict(row)
    options = data.get("pricing_options", [])
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except (ValueError, TypeError):
            options = []
    data["pricing_options"] = options or []
    return data


async def create_product(
    pool, *, name, description, category, price, currency="USD",
    country="", features="", compatibility="", limits="",
    pricing_options=None
):
    row = await pool.fetchrow(
        """
        INSERT INTO products
        (name, description, category, price, currency, country,
         features, compatibility, limits, pricing_options)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING *;
        """,
        name, description, category, Decimal(str(price)), currency,
        country, features, compatibility, limits,
        json.dumps(pricing_options or []),
    )
    return _product_to_dict(row)


async def list_products(
    pool, *, active_only=True, category=None, country=None
):
    conditions = []
    args = []

    if active_only:
        conditions.append("active=TRUE")
    if category:
        args.append(category)
        conditions.append(f"category=${len(args)}")
    if country:
        args.append(country)
        conditions.append(f"country=${len(args)}")

    query = "SELECT * FROM products"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC;"

    rows = await pool.fetch(query, *args)
    return [_product_to_dict(row) for row in rows]


async def list_product_countries(pool):
    rows = await pool.fetch(
        """
        SELECT DISTINCT country FROM products
        WHERE active=TRUE AND country <> ''
        ORDER BY country;
        """
    )
    return [row["country"] for row in rows]


async def get_product(pool, product_id):
    row = await pool.fetchrow(
        "SELECT * FROM products WHERE id=$1;", product_id
    )
    return _product_to_dict(row) if row else None


async def update_product(
    pool, product_id, *, name, description, category, price,
    currency, active, country="", features="", compatibility="",
    limits="", pricing_options=None
):
    row = await pool.fetchrow(
        """
        UPDATE products SET
            name=$2, description=$3, category=$4, price=$5,
            currency=$6, active=$7, country=$8, features=$9,
            compatibility=$10, limits=$11, pricing_options=$12,
            updated_at=now()
        WHERE id=$1 RETURNING *;
        """,
        product_id, name, description, category, Decimal(str(price)),
        currency, active, country, features, compatibility, limits,
        json.dumps(pricing_options or []),
    )
    return _product_to_dict(row) if row else None


async def deactivate_product(pool, product_id):
    result = await pool.execute(
        """
        UPDATE products SET active=FALSE, updated_at=now()
        WHERE id=$1 AND active=TRUE;
        """,
        product_id,
    )
    return result.endswith("1")


# --- Wallet catalogue seed data ---------------------------------------------
# Normal customer prices only. No reseller prices or rental services.

WALLET_CATALOG = [
    {
        "name": "Gomoney",
        "country": "Nigeria",
        "description": "Nigerian digital wallet with included Mastercard cards.",
        "features": "Includes 3 Mastercards.",
        "compatibility": "Android and iOS.",
        "price": "35",
    },
    {
        "name": "Timon",
        "country": "Nigeria",
        "description": "Nigerian wallet with included card options.",
        "features": "Up to 5 Visa cards on iOS or up to 5 Verve cards on Android.",
        "compatibility": "Android and iOS.",
        "price": "25",
    },
    {
        "name": "Kuda",
        "country": "Nigeria",
        "description": "Kuda wallet with one included Verve card.",
        "features": "Includes 1 Verve card.",
        "compatibility": "Android.",
        "price": "10",
        "pricing_options": [
            {"label": "Tier 1", "price": "10"},
            {"label": "Tier 2", "price": "20"},
            {"label": "Tier 3", "price": "30"},
            {"label": "Tier 3+", "price": "40"},
        ],
    },
    {
        "name": "Paga (Pagatier)",
        "country": "Nigeria",
        "description": "Paga wallet with Visa card options.",
        "features": "Up to 2 Visa cards.",
        "compatibility": "Android and iOS.",
        "price": "30",
        "pricing_options": [
            {"label": "Tier 2", "price": "30"},
            {"label": "Tier 3", "price": "40"},
        ],
    },
    {
        "name": "Root by Sudo",
        "country": "Nigeria",
        "description": "Wallet with virtual card options and crypto-to-NGN support.",
        "features": "Up to 5 virtual cards. Fast crypto-to-NGN support.",
        "compatibility": "Android and iOS.",
        "price": "30",
    },
    {
        "name": "Spenda",
        "country": "Nigeria",
        "description": "Wallet for converting crypto to Naira and making online payments.",
        "features": "Convert crypto to Naira. Supports subscription and online payments.",
        "compatibility": "Android and iOS.",
        "price": "30",
    },
    {
        "name": "YangaPlug",
        "country": "Nigeria",
        "description": "Wallet with Naira and Dollar card options.",
        "features": "Up to 5 Naira Mastercard cards and up to 3 Dollar cards.",
        "compatibility": "Android, iOS and PC.",
        "price": "40",
    },
    {
        "name": "V Wallet",
        "country": "Nigeria",
        "description": "Wallet managed using eSIM and Gmail; no mobile app required.",
        "features": "Up to 10 Verve cards.",
        "compatibility": "Android devices and PC.",
        "price": "40",
        "pricing_options": [
            {"label": "Without eSIM", "price": "40"},
            {"label": "With eSIM", "price": "50"},
        ],
    },
    {
        "name": "Raven Bank",
        "country": "Nigeria",
        "description": "Wallet with multiple-currency card options.",
        "features": "Multiple currency cards.",
        "compatibility": "Android and iOS.",
        "price": "40",
        "pricing_options": [
            {"label": "Tier 2", "price": "40"},
            {"label": "Tier 3", "price": "50"},
            {"label": "Tier 4", "price": "60"},
        ],
    },
    {
        "name": "FUPS Premium",
        "country": "Turkey",
        "description": "Turkish wallet with premium account features.",
        "features": "10 cards per year; monthly limit 1M TRY; 3DS supported via app.",
        "price": "110",
    },
    {
        "name": "TURAN Premium",
        "country": "Turkey",
        "description": "Turkish wallet with premium account features.",
        "features": "Monthly limit 1.5M TRY; 4 cards only; 3DS to app not supported. Accounts created with temporary phone numbers may become inaccessible after logout. Own eSIM is more reliable.",
        "price": "100",
    },
    {
        "name": "MobiKwik",
        "country": "India",
        "description": "Digital wallet with UPI payment features.",
        "features": "UPI payments and transfers; online, in-store and QR payments; Pocket UPI without linking a bank account; payment tracking.",
        "compatibility": "Android and iOS.",
        "price": "170",
    },
    {
        "name": "Amazon Pay",
        "country": "India",
        "description": "Amazon Pay wallet and UPI features.",
        "features": "Amazon Pay Balance; UPI QR payments; Amazon purchases and online services; available through the Amazon app.",
        "price": "160",
    },
    {
        "name": "Jio Payments Bank",
        "country": "India",
        "description": "Digital savings bank account.",
        "features": "Zero-balance account option; UPI and instant transfers; physical and virtual RuPay Platinum debit card; ATM withdrawals and balance enquiries.",
        "price": "170",
    },
    {
        "name": "Airtel Payments Bank",
        "country": "India",
        "description": "Digital savings bank account.",
        "features": "UPI and money transfers; virtual Mastercard debit card; ATM and cash withdrawal services; bill payments; QR payments; rewards.",
        "price": "200",
    },
    {
        "name": "Fino Payments Bank",
        "country": "India",
        "description": "Digital banking and savings account.",
        "features": "RuPay Platinum debit card; online and offline card payments; contactless payments; rewards; EMV chip and PIN security. Card usage depends on provider eligibility and terms.",
        "price": "180",
    },
    {
        "name": "NayaPay",
        "country": "Pakistan",
        "description": "Pakistan digital wallet.",
        "features": "Digital wallet for payments and transfers.",
        "compatibility": "Android and iOS.",
        "price": "45",
    },
    {
        "name": "SadaPay",
        "country": "Pakistan",
        "description": "Pakistan digital wallet.",
        "features": "Virtual and physical card support; digital payments and transfers.",
        "compatibility": "Android and iOS.",
        "price": "45",
    },
    {
        "name": "OPay Pakistan Wallet",
        "country": "Pakistan",
        "description": "Pakistan wallet with its associated card included.",
        "features": "Wallet purchase includes its associated Pakistan card.",
        "price": "60",
    },
    {
        "name": "UKRSIBBANK",
        "country": "Ukraine",
        "description": "Ukraine wallet/account product.",
        "features": "Monthly limit 100,000 UAH; 5 cards included; eSIM included.",
        "price": "160",
    },
    {
        "name": "PIVDENNY",
        "country": "Ukraine",
        "description": "Ukraine wallet/account product.",
        "features": "Monthly limit 100,000 UAH; 1 card included; eSIM included.",
        "price": "150",
    },
    {
        "name": "Jago Bank",
        "country": "Indonesia",
        "description": "Indonesia digital banking account.",
        "features": "High-limit account; unlimited virtual cards; eSIM included.",
        "compatibility": "Android and iOS.",
        "price": "60",
    },
]


async def seed_wallet_catalog(pool):
    """
    Add the supplied wallet catalogue once.
    Existing matching products are not overwritten.
    """

    added = 0

    async with pool.acquire() as conn, conn.transaction():
        for item in WALLET_CATALOG:
            exists = await conn.fetchval(
                """
                SELECT id FROM products
                WHERE lower(name)=lower($1)
                  AND lower(country)=lower($2)
                LIMIT 1;
                """,
                item["name"], item["country"],
            )

            if exists:
                continue

            await conn.execute(
                """
                INSERT INTO products
                (name, description, category, price, currency, country,
                 features, compatibility, pricing_options)
                VALUES ($1,$2,'Wallets',$3,'USD',$4,$5,$6,$7);
                """,
                item["name"],
                item["description"],
                Decimal(item["price"]),
                item["country"],
                item.get("features", ""),
                item.get("compatibility", ""),
                json.dumps(item.get("pricing_options", [])),
            )
            added += 1

    logger.info("Wallet catalogue seed completed. Added %s products.", added)
    return added
