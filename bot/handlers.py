
"""Telegram update handlers for NGFLUX STORE."""

import logging
import os
import secrets
from decimal import Decimal, InvalidOperation
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import commands, db, commerce

logger = logging.getLogger(__name__)

DB_KEY = "db"
_LOCAL_MESSAGE_COUNTS: dict[int, int] = {}

BOT_COMMANDS = (
    ("start", "Open the main menu"),
    ("shop", "Browse store products"),
    ("orders", "View your previous orders"),
    ("payment", "View payment methods"),
    ("support", "Contact customer support"),
    ("help", "Show help"),
    ("about", "About NGFLUX STORE"),
)

MENU_SHOP = "🛍️ Shop Products"
MENU_ORDERS = "📦 My Orders"
MENU_PAYMENT = "💳 Payment Methods"
MENU_HOME = "🏠 Main Menu"
MENU_SUPPORT = "💬 Customer Support"
MENU_HELP = "Help"
MENU_ABOUT = "About"
MENU_PING = "Ping"

HELP_TEXT = """NGFLUX STORE Commands:

/start - Open the main menu
/shop - Browse products
/orders - View your previous orders
/payment - View payment methods
/support - Contact customer support
/help - Show help
/about - Store information"""

DYNAMIC_CALLBACK_PREFIX = "command:"
PRODUCT_CALLBACK_PREFIX = "product:"

CATEGORIES = [
    "💳 Wallets & Virtual Cards",
    "🎮 Gaming & Discord",
    "🎵 Entertainment & Subscriptions",
    "📧 Digital Accounts",
    "📦 Other Products",
]

CATEGORY_VALUES = {
    "wallets": "Wallets & Virtual Cards",
    "wallets & virtual cards": "Wallets & Virtual Cards",
    "wallets and virtual cards": "Wallets & Virtual Cards",
    "gaming": "Gaming & Discord",
    "gaming & discord": "Gaming & Discord",
    "discord": "Gaming & Discord",
    "entertainment": "Entertainment & Subscriptions",
    "entertainment & subscriptions": "Entertainment & Subscriptions",
    "subscriptions": "Entertainment & Subscriptions",
    "digital accounts": "Digital Accounts",
    "digital account": "Digital Accounts",
    "digital products": "Digital Accounts",
    "accounts": "Digital Accounts",
    "other": "Other Products",
    "other products": "Other Products",
    "others": "Other Products",
}

CATEGORY_CALLBACKS = {
    "wallets": "Wallets & Virtual Cards",
    "gaming": "Gaming & Discord",
    "entertainment": "Entertainment & Subscriptions",
    "accounts": "Digital Accounts",
    "other": "Other Products",
}

(
    ADD_NAME,
    ADD_DESCRIPTION,
    ADD_CATEGORY,
    ADD_COUNTRY,
    ADD_FEATURES,
    ADD_COMPATIBILITY,
    ADD_LIMITS,
    ADD_PRICE,
    ADD_CURRENCY,
    EDIT_NAME,
    EDIT_DESCRIPTION,
    EDIT_CATEGORY,
    EDIT_PRICE,
    EDIT_CURRENCY,
    EDIT_ACTIVE,
) = range(15)


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _is_admin(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False

    admin_ids = {
        value.strip()
        for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if value.strip()
    }
    return str(user.id) in admin_ids


async def _require_admin(update: Update) -> bool:
    if _is_admin(update):
        return True

    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "⛔ You are not authorized to use this admin command."
        )
    return False


def _get_pool(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data.get(DB_KEY)


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [MENU_SHOP, MENU_ORDERS],
            [MENU_PAYMENT, MENU_SUPPORT],
            [MENU_HOME],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose an NGFLUX STORE option",
    )


def _dynamic_commands_text() -> str:
    items = commands.menu_commands()
    if not items:
        return ""

    lines = [f"/{name} - {description}" for name, description in items]
    return "\n\nAdditional commands:\n" + "\n".join(lines)


def _dynamic_commands_keyboard():
    items = commands.menu_commands()
    if not items:
        return None

    buttons = [
        InlineKeyboardButton(
            description or f"/{name}",
            callback_data=f"{DYNAMIC_CALLBACK_PREFIX}{name}",
        )
        for name, description in items
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _format_price(product: dict) -> str:
    return (
        f"{product.get('currency', 'USD')} "
        f"{Decimal(str(product.get('price', 0))):,.2f}"
    )


def _parse_price(value: str) -> Decimal | None:
    try:
        price = Decimal(value.strip())
        if not price.is_finite() or price < 0:
            return None
        return price
    except (InvalidOperation, ValueError):
        return None


def _canonical_category(value: str) -> str | None:
    return CATEGORY_VALUES.get(value.strip().lower())


def _category_keyboard() -> InlineKeyboardMarkup:
    callbacks = ["wallets", "gaming", "entertainment", "accounts", "other"]
    rows = [
        [
            InlineKeyboardButton(
                CATEGORIES[i],
                callback_data=f"nav:cat:{callbacks[i]}",
            )
        ]
        for i in range(len(CATEGORIES))
    ]
    return InlineKeyboardMarkup(rows)


async def _ensure_wallet_catalog(pool) -> None:
    """Seed the supplied wallet catalogue once; keep old seeded records organised."""
    if not hasattr(db, "seed_wallet_catalog"):
        return

    await db.seed_wallet_catalog(pool)

    await pool.execute(
        """
        UPDATE products
        SET category = 'Wallets & Virtual Cards',
            updated_at = NOW()
        WHERE LOWER(category) = 'wallets'
        """
    )


def _back_to_categories_button():
    return InlineKeyboardButton(
        "⬅️ Back to Categories",
        callback_data="nav:categories",
    )


def _product_button(product: dict):
    return InlineKeyboardButton(
        f"{product['name']} — {_format_price(product)}",
        callback_data=f"product:{product['id']}",
    )


def _product_list_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    rows = [[_product_button(product)] for product in products]
    rows.append([_back_to_categories_button()])
    return InlineKeyboardMarkup(rows)


def _country_keyboard(countries: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"🌍 {country}",
                callback_data=f"nav:country:{i}",
            )
        ]
        for i, country in enumerate(countries)
    ]
    rows.append([_back_to_categories_button()])
    return InlineKeyboardMarkup(rows)


def _normalise_legacy_category(category: str) -> str:
    """Map old and current category names to official store categories."""
    value = (category or "").strip().lower()

    if value in (
        "wallets",
        "wallet",
        "virtual cards",
        "cards",
        "wallets & virtual cards",
        "wallets and virtual cards",
    ):
        return "Wallets & Virtual Cards"

    if value in (
        "discord",
        "gaming",
        "games",
        "gaming & discord",
    ):
        return "Gaming & Discord"

    if value in (
        "subscriptions",
        "subscription",
        "entertainment",
        "entertainment & subscriptions",
    ):
        return "Entertainment & Subscriptions"

    if value in (
        "accounts",
        "account",
        "digital account",
        "digital accounts",
        "digital products",
    ):
        return "Digital Accounts"

    if value in (
        "other",
        "others",
        "other products",
    ):
        return "Other Products"

    return (
        category.strip()
        if category and category.strip()
        else "Other Products"
    )


def _format_pricing_options(product: dict) -> str:
    options = product.get("pricing_options")
    if not options:
        return ""

    if isinstance(options, str):
        import json
        try:
            options = json.loads(options)
        except (ValueError, TypeError):
            return ""

    if not isinstance(options, list):
        return ""

    lines = ["\n💰 Available price options:"]

    for option in options:
        if isinstance(option, dict):
            label = (
                option.get("name")
                or option.get("label")
                or option.get("tier")
            )
            price = option.get("price")
            currency = option.get("currency") or product.get(
                "currency", "USD"
            )

            if label and price is not None:
                lines.append(f"• {label}: {currency} {price}")
            elif price is not None:
                lines.append(f"• {currency} {price}")

    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Start and main menu
# ---------------------------------------------------------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    pool = _get_pool(context)
    is_new = True

    if pool is not None:
        is_new = await db.upsert_user(
            pool,
            user.id,
            user.username,
            user.first_name,
        )

    name = user.first_name or "friend"
    greeting = "Welcome" if is_new else "Welcome back"

    await message.reply_text(
        f"{greeting}, {name}! Welcome to NGFLUX STORE.\n\n"
        "Your Trusted Digital Marketplace.\n\n"
        "Choose an option from the menu below to get started.",
        reply_markup=_main_menu_keyboard(),
    )

    if _is_admin(update):
        await message.reply_text(
            "🔐 Admin access enabled.\n\n"
            "Product management:\n"
            "/addproduct - Add a product\n"
            "/products - View all products\n"
            "/editproduct ID - Edit a product\n"
            "/deleteproduct ID - Deactivate a product\n"
            "/cancel - Cancel an active operation"
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is not None:
        await message.reply_text(
            HELP_TEXT + _dynamic_commands_text(),
            reply_markup=_main_menu_keyboard(),
        )


async def about(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is not None:
        await message.reply_text(
            "NGFLUX STORE\n"
            "Your Trusted Digital Marketplace.\n\n"
            "We offer digital wallets, virtual cards, gaming products, "
            "subscriptions and digital accounts.\n\n"
            "Customer Support: @Ngfluxstore",
            reply_markup=_main_menu_keyboard(),
        )


async def ping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is not None:
        await message.reply_text("pong")


# ---------------------------------------------------------------------------
# Customer services
# ---------------------------------------------------------------------------


async def payment_methods(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    payment_text = (
        "💳 NGFLUX STORE — PAYMENT METHODS\n\n"
        "1️⃣ BINANCE PAY\n"
        "Binance Pay ID: 186299385\n"
        "Account name: ngfluxstore\n\n"
        "2️⃣ BYBIT PAY\n"
        "Bybit ID: 60400710\n"
        "Account name: ngfluxstore\n\n"
        "3️⃣ USDT — BEP20\n"
        "Network: BNB Smart Chain (BEP20)\n"
        "Address:\n"
        "0x895a3a2d4f6401d5a418ed4c1e865aa9e78e15aa\n\n"
        "4️⃣ LITECOIN (LTC)\n"
        "Address:\n"
        "LZZhKrbf8CxsaykWuvHFBPKauBy9BzNwJN\n\n"
        "⚠️ IMPORTANT PAYMENT NOTICE\n"
        "• Always send the exact requested amount.\n"
        "• Verify the network and address before sending.\n"
        "• Never send funds to an unverified address.\n"
        "• After payment, open a support ticket and provide "
        "your TXID or payment screenshot.\n"
        "• Payments are non-refundable once confirmed.\n\n"
        "🎫 Need assistance? Contact @Ngfluxstore."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎫 Contact Support",
                url="https://t.me/Ngfluxstore",
            )
        ]
    ])

    await message.reply_text(
        payment_text,
        reply_markup=keyboard,
    )

PAYMENT_SUPPORT_ORDER = "PAYMENT_SUPPORT_ORDER"
PAYMENT_SUPPORT_ISSUE = "PAYMENT_SUPPORT_ISSUE"


async def payment_support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None:
        return ConversationHandler.END

    pool = context.application.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "Payment support is temporarily unavailable. "
            "Please try again later."
        )
        return ConversationHandler.END

    context.user_data["payment_support"] = {}

    await message.reply_text(
        "🛟 NGFLUX STORE — PAYMENT SUPPORT\n\n"
        "Let's create a support ticket for you.\n\n"
        "Please enter your order number.\n"
        "If you don't have one, type NONE.\n\n"
        "Send /cancel to stop."
    )

    return PAYMENT_SUPPORT_ORDER


async def payment_support_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return PAYMENT_SUPPORT_ORDER

    order_number = message.text.strip()

    if len(order_number) > 100:
        await message.reply_text(
            "That order number is too long. Please try again."
        )
        return PAYMENT_SUPPORT_ORDER

    context.user_data["payment_support"]["order_number"] = order_number

    await message.reply_text(
        "Thank you!\n\n"
        "Now describe the payment or order problem you are experiencing."
    )

    return PAYMENT_SUPPORT_ISSUE


async def payment_support_issue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None or not message.text:
        return PAYMENT_SUPPORT_ISSUE

    issue = message.text.strip()

    if len(issue) < 10:
        await message.reply_text(
            "Please provide a little more detail "
            "(at least 10 characters)."
        )
        return PAYMENT_SUPPORT_ISSUE

    if len(issue) > 3000:
        await message.reply_text(
            "Your description is too long. "
            "Please keep it under 3,000 characters."
        )
        return PAYMENT_SUPPORT_ISSUE

    pool = context.application.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "We couldn't save your ticket because the database "
            "is temporarily unavailable. Please try again later."
        )
        return ConversationHandler.END

    ticket_number = f"NGF-SUP-{secrets.token_hex(4).upper()}"
    order_number = context.user_data["payment_support"].get(
        "order_number", "NONE"
    )

    try:
        await db.create_support_ticket(
            pool,
            ticket_number=ticket_number,
            telegram_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            order_number=order_number,
            issue=issue,
        )
    except Exception:
        logger.exception("Failed to save payment support ticket.")
        await message.reply_text(
            "Sorry, we couldn't save your ticket. "
            "Please try again later."
        )
        return ConversationHandler.END

    admin_ids = {
        value.strip()
        for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if value.strip()
    }

    admin_message = (
        "🎫 NEW NGFLUX SUPPORT TICKET\n\n"
        f"Ticket: {ticket_number}\n"
        f"Customer: {user.first_name or 'Unknown'}\n"
        f"Username: @{user.username or 'Not set'}\n"
        f"Telegram ID: {user.id}\n"
        f"Order number: {order_number}\n\n"
        f"Problem:\n{issue}\n\n"
        "Status: OPEN"
    )

    for admin_id in admin_ids:
        try:
            await context.bot.send_message(
                chat_id=int(admin_id),
                text=admin_message,
            )
        except Exception:
            logger.exception(
                "Could not notify support admin %s.",
                admin_id,
            )

    if not admin_ids:
        logger.warning(
            "Support ticket %s saved, but no admin IDs are configured.",
            ticket_number,
        )

    await message.reply_text(
        "✅ YOUR SUPPORT TICKET HAS BEEN CREATED!\n\n"
        f"Ticket number: {ticket_number}\n"
        f"Order number: {order_number}\n"
        "Status: OPEN\n\n"
        "Our support team has received your request "
        "and will review it.\n\n"
        "Thank you for choosing NGFLUX STORE! 💙"
    )

    context.user_data.pop("payment_support", None)

    return ConversationHandler.END


async def cancel_payment_support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    context.user_data.pop("payment_support", None)

    if update.effective_message:
        await update.effective_message.reply_text(
            "Your support request has been cancelled."
        )

    return ConversationHandler.END



async def customer_support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💬 Contact NGFLUX Support",
            url="https://t.me/Ngfluxstore",
        )
    ]])

    await message.reply_text(
        "💬 NGFLUX STORE CUSTOMER SUPPORT\n\n"
        "Need help with an order, payment, product or service?\n\n"
        "Tap below to contact our support team.",
        reply_markup=keyboard,
    )


async def my_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    pool = _get_pool(context)

    if pool is None:
        await message.reply_text(
            "Your order history is temporarily unavailable."
        )
        return

    try:
        orders = await pool.fetch(
            """
            SELECT order_number, product_name, quantity,
                   total_amount, currency, status, created_at
            FROM orders
            WHERE telegram_id = $1
            ORDER BY created_at DESC
            LIMIT 10
            """,
            user.id,
        )
    except Exception:
        logger.exception("Could not retrieve customer orders")
        await message.reply_text(
            "I couldn't retrieve your orders. Contact @Ngfluxstore."
        )
        return

    if not orders:
        await message.reply_text(
            "📦 YOUR ORDERS\n\n"
            "You don't have any orders yet.\n"
            "Visit Shop Products to explore what's available.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    lines = ["📦 YOUR RECENT ORDERS\n"]

    for order in orders:
        lines.append(
            f"\nOrder: {order['order_number']}\n"
            f"Product: {order['product_name']}\n"
            f"Quantity: {order['quantity']}\n"
            f"Total: {order['currency']} {order['total_amount']:,.2f}\n"
            f"Status: {order['status']}\n"
            f"Date: {order['created_at']}"
        )

    await message.reply_text(
        "\n".join(lines),
        reply_markup=_main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Customer catalogue navigation
# ---------------------------------------------------------------------------

async def shop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    pool = _get_pool(context)

    if pool is None:
        await message.reply_text(
            "Our product catalogue is temporarily unavailable."
        )
        return

    try:
        await _ensure_wallet_catalog(pool)
    except Exception:
        logger.exception("Wallet catalogue initialisation failed")

    context.user_data.pop("shop_country", None)
    context.user_data.pop("shop_category", None)

    await message.reply_text(
        "🛍️ NGFLUX STORE\n\n"
        "Choose a category to explore our products:",
        reply_markup=_category_keyboard(),
    )


async def show_categories(
    query,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data.pop("shop_country", None)
    context.user_data.pop("shop_category", None)

    await query.edit_message_text(
        "🛍️ NGFLUX STORE\n\nChoose a product category:",
        reply_markup=_category_keyboard(),
    )


async def category_navigation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None or query.data is None:
        return

    await query.answer()
    pool = _get_pool(context)

    if pool is None:
        await query.edit_message_text(
            "The catalogue is temporarily unavailable."
        )
        return

    if query.data == "nav:categories":
        await show_categories(query, context)
        return

    slug = query.data.removeprefix("nav:cat:")
    category = CATEGORY_CALLBACKS.get(slug)

    if category is None:
        return

    context.user_data["shop_category"] = category
    context.user_data.pop("shop_country", None)

    # Wallet category: show available countries.
    if category == "Wallets & Virtual Cards":
        try:
            await _ensure_wallet_catalog(pool)

            countries = await db.list_product_countries(pool)
            countries = sorted({
                str(country).strip()
                for country in countries
                if country and str(country).strip()
            })

        except Exception:
            logger.exception("Could not retrieve wallet countries")
            await query.edit_message_text(
                "I couldn't load wallet countries right now. Please try again."
            )
            return

        context.user_data["shop_countries"] = countries

        if not countries:
            await query.edit_message_text(
                "🌍 No wallet countries are currently available.",
                reply_markup=InlineKeyboardMarkup([[
                    _back_to_categories_button()
                ]]),
            )
            return

        await query.edit_message_text(
            "💳 WALLETS & VIRTUAL CARDS\n\n"
            "Choose a country to view its available wallets:",
            reply_markup=_country_keyboard(countries),
        )
        return

    # Search current and older category labels.
    category_aliases = {
        "Gaming & Discord": [
            "Gaming & Discord",
            "Gaming",
            "Discord",
        ],
        "Entertainment & Subscriptions": [
            "Entertainment & Subscriptions",
            "Entertainment",
            "Subscriptions",
        ],
        "Digital Accounts": [
            "Digital Accounts",
            "Digital Account",
            "Accounts",
            "Digital Products",
        ],
        "Other Products": [
            "Other Products",
            "Other",
            "Others",
        ],
    }

    products = []
    seen_product_ids = set()

    try:
        for stored_category in category_aliases.get(
            category,
            [category],
        ):
            matching_products = await db.list_products(
                pool,
                active_only=True,
                category=stored_category,
            )

            for product in matching_products:
                product_id = product.get("id")

                if product_id not in seen_product_ids:
                    seen_product_ids.add(product_id)
                    products.append(product)

    except Exception:
        logger.exception("Could not retrieve category products")
        await query.edit_message_text(
            "I couldn't load these products right now. Please try again."
        )
        return

    if not products:
        await query.edit_message_text(
            f"{category}\n\n"
            "There are currently no products in this category.\n"
            "Please check back soon.",
            reply_markup=InlineKeyboardMarkup([[
                _back_to_categories_button()
            ]]),
        )
        return

    await query.edit_message_text(
        f"{category}\n\nSelect a product to view its details:",
        reply_markup=_product_list_keyboard(products),
    )


async def country_navigation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None or query.data is None:
        return

    await query.answer()
    pool = _get_pool(context)

    if pool is None:
        await query.edit_message_text(
            "The catalogue is temporarily unavailable."
        )
        return

    try:
        index = int(query.data.removeprefix("nav:country:"))
        countries = context.user_data.get("shop_countries", [])
        country = countries[index]

    except (ValueError, IndexError, TypeError):
        await query.edit_message_text(
            "That country selection has expired. Please open the shop again."
        )
        return

    context.user_data["shop_country"] = country

    products = await db.list_products(
        pool,
        active_only=True,
        category="Wallets & Virtual Cards",
        country=country,
    )

    if not products:
        await query.edit_message_text(
            f"🌍 {country} Wallets\n\n"
            "No wallets are currently available for this country.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ Back to Countries",
                    callback_data="nav:cat:wallets",
                )],
                [_back_to_categories_button()],
            ]),
        )
        return

    rows = [[_product_button(product)] for product in products]

    rows.append([InlineKeyboardButton(
        "⬅️ Back to Countries",
        callback_data="nav:cat:wallets",
    )])

    await query.edit_message_text(
        f"🌍 {country.upper()} WALLETS\n\n"
        "Select a wallet to view its features and price:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def product_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None or query.data is None:
        return

    await query.answer()
    pool = _get_pool(context)

    if pool is None:
        await query.edit_message_text(
            "The catalogue is temporarily unavailable."
        )
        return

    try:
        product_id = int(
            query.data.removeprefix(PRODUCT_CALLBACK_PREFIX)
        )
    except ValueError:
        return

    product = await db.get_product(pool, product_id)

    if product is None or not product.get("active"):
        await query.edit_message_text(
            "Sorry, this product is no longer available."
        )
        return

    category = _normalise_legacy_category(
        product.get("category", "")
    )
    country = product.get("country") or ""
    description = product.get("description") or "No description available."
    features = product.get("features") or ""
    compatibility = product.get("compatibility") or ""
    limits = product.get("limits") or ""

    parts = [
        f"🛍️ {product['name']}",
        "",
        f"📂 Category: {category}",
    ]

    if country:
        parts.append(f"🌍 Country: {country}")

    parts.extend(["", f"📝 Description: {description}"])

    if features:
        parts.extend(["", f"⭐ Features:\n{features}"])

    if compatibility:
        parts.extend(["", f"📱 Compatibility: {compatibility}"])

    if limits:
        parts.extend(["", f"📊 Limits: {limits}"])

    parts.extend([
        "",
        f"💰 Price: {_format_price(product)}",
    ])

    options_text = _format_pricing_options(product)

    if options_text:
        parts.append(options_text)

    parts.extend([
        "",
        "🛒 Tap Buy Now to create your order.",
        "💳 Payment is verified manually before processing.",
        "",
        "Support: @Ngfluxstore",
    ])

    if category == "Wallets & Virtual Cards" and country:
        back_callback = "nav:cat:wallets"
        back_label = "⬅️ Back to Country Wallets"

    elif category == "Wallets & Virtual Cards":
        back_callback = "nav:cat:wallets"
        back_label = "⬅️ Back to Countries"

    else:
        slug = {
            "Gaming & Discord": "gaming",
            "Entertainment & Subscriptions": "entertainment",
            "Digital Accounts": "accounts",
            "Other Products": "other",
        }.get(category, "other")

        back_callback = f"nav:cat:{slug}"
        back_label = "⬅️ Back to Products"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🛒 Buy Now",
            callback_data=f"checkout:create:{product_id}",
        )],
        [InlineKeyboardButton(
            "💬 Contact Support",
            url="https://t.me/Ngfluxstore",
        )],
        [InlineKeyboardButton(
            back_label,
            callback_data=back_callback,
        )],
        [_back_to_categories_button()],
    ])

    await query.edit_message_text(
        "\n".join(parts),
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Customer checkout
# ---------------------------------------------------------------------------

async def create_checkout_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None or query.data is None:
        return

    user = update.effective_user
    pool = _get_pool(context)

    if user is None or pool is None:
        await query.answer(
            "Checkout is temporarily unavailable. Please try again later.",
            show_alert=True,
        )
        return

    try:
        product_id = int(
            query.data.removeprefix("checkout:create:")
        )
    except ValueError:
        await query.answer(
            "Invalid product selection.",
            show_alert=True,
        )
        return

    await query.answer("Creating your order...")

    try:
        order = await commerce.create_order(
            pool,
            telegram_id=user.id,
            product_id=product_id,
            quantity=1,
        )

    except Exception:
        logger.exception(
            "Checkout order creation failed for Telegram user %s "
            "and product %s",
            user.id,
            product_id,
        )

        if query.message is not None:
            await query.message.reply_text(
                "❌ We couldn't create your order.\n\n"
                "Please try again or contact @Ngfluxstore."
            )
        return

    order_number = order["order_number"]
    product_name = order["product_name"]
    quantity = order["quantity"]
    total_amount = Decimal(str(order["total_amount"]))
    currency = order["currency"]

    confirmation = (
        "✅ ORDER CREATED SUCCESSFULLY!\n\n"
        f"📦 Order: {order_number}\n"
        f"🛍️ Product: {product_name}\n"
        f"🔢 Quantity: {quantity}\n"
        f"💰 Total: {currency} {total_amount:,.2f}\n\n"
        "📌 Status: Awaiting Payment\n\n"
        "Your order has been saved successfully.\n"
        "Use /payment to view available payment methods.\n\n"
        "Contact @Ngfluxstore to receive payment instructions.\n"
        "Your order will be processed after payment is verified."
    )

    try:
        await query.edit_message_text(
            confirmation,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💬 Contact Support",
                    url="https://t.me/Ngfluxstore",
                )
            ]]),
        )
    except Exception:
        logger.exception(
            "Order %s was created, but the confirmation message "
            "could not be edited",
            order_number,
        )

        if query.message is not None:
            await query.message.reply_text(confirmation)


# ---------------------------------------------------------------------------
# Admin: list products
# ---------------------------------------------------------------------------

async def admin_products(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _require_admin(update):
        return

    message = update.effective_message
    pool = _get_pool(context)

    if message is None:
        return

    if pool is None:
        await message.reply_text("Database is unavailable.")
        return

    products = await db.list_products(
        pool,
        active_only=False,
    )

    if not products:
        await message.reply_text(
            "📦 No products have been added yet.\nUse /addproduct to add one."
        )
        return

    lines = ["📦 NGFLUX STORE — PRODUCT MANAGEMENT\n"]

    for product in products[:40]:
        status = "🟢 Active" if product["active"] else "🔴 Inactive"

        lines.append(
            f"\nID: {product['id']}\n"
            f"Name: {product['name']}\n"
            f"Category: {_normalise_legacy_category(product['category'])}\n"
            f"Country: {product.get('country') or '—'}\n"
            f"Price: {_format_price(product)}\n"
            f"Status: {status}"
        )

    lines.append(
        "\n\n/addproduct\n/editproduct ID\n/deleteproduct ID"
    )

    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Admin: add product
# ---------------------------------------------------------------------------

async def add_product_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if not await _require_admin(update):
        return ConversationHandler.END

    message = update.effective_message

    if message is None:
        return ConversationHandler.END

    if _get_pool(context) is None:
        await message.reply_text("Database is unavailable.")
        return ConversationHandler.END

    context.user_data["new_product"] = {}

    await message.reply_text(
        "➕ ADD PRODUCT\n\nStep 1: Enter the product name."
    )

    return ADD_NAME


async def add_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_NAME

    name = message.text.strip()

    if not name or len(name) > 150:
        await message.reply_text("Enter a valid product name.")
        return ADD_NAME

    context.user_data["new_product"]["name"] = name

    await message.reply_text(
        "Step 2: Enter the product description.\nSend - for no description."
    )

    return ADD_DESCRIPTION


async def add_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_DESCRIPTION

    description = message.text.strip()

    context.user_data["new_product"]["description"] = (
        "" if description == "-" else description
    )

    category_list = "\n".join(f"• {item}" for item in CATEGORIES)

    await message.reply_text(
        "Step 3: Enter ONE of these exact categories:\n\n"
        f"{category_list}"
    )

    return ADD_CATEGORY


async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_CATEGORY

    category = _canonical_category(message.text)

    if category is None:
        await message.reply_text(
            "Please use one of the five official category names shown above."
        )
        return ADD_CATEGORY

    context.user_data["new_product"]["category"] = category

    if category == "Wallets & Virtual Cards":
        await message.reply_text(
            "Step 4: Enter the wallet's country.\n"
            "Example: Nigeria, Turkey, India, Pakistan, Ukraine or Indonesia."
        )
        return ADD_COUNTRY

    context.user_data["new_product"]["country"] = ""

    await message.reply_text(
        "Step 4: Enter product features, one per line.\nSend - if none."
    )

    return ADD_FEATURES


async def add_country(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_COUNTRY

    country = message.text.strip()

    if not country or len(country) > 60:
        await message.reply_text("Please enter a valid country name.")
        return ADD_COUNTRY

    context.user_data["new_product"]["country"] = country

    await message.reply_text(
        "Step 5: Enter wallet features, one per line.\nSend - if none."
    )

    return ADD_FEATURES


async def add_features(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_FEATURES

    value = message.text.strip()

    context.user_data["new_product"]["features"] = (
        "" if value == "-" else value
    )

    await message.reply_text(
        "Next: Enter device/app compatibility.\n"
        "Example: Android & iOS. Send - if not applicable."
    )

    return ADD_COMPATIBILITY


async def add_compatibility(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_COMPATIBILITY

    value = message.text.strip()

    context.user_data["new_product"]["compatibility"] = (
        "" if value == "-" else value
    )

    await message.reply_text(
        "Next: Enter account limits or other important details.\n"
        "Send - if none."
    )

    return ADD_LIMITS


async def add_limits(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_LIMITS

    value = message.text.strip()

    context.user_data["new_product"]["limits"] = (
        "" if value == "-" else value
    )

    await message.reply_text(
        "Next: Enter the product price, for example 60 or 3.50."
    )

    return ADD_PRICE


async def add_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_PRICE

    price = _parse_price(message.text)

    if price is None:
        await message.reply_text(
            "Invalid price. Enter an amount such as 60 or 3.50."
        )
        return ADD_PRICE

    context.user_data["new_product"]["price"] = price

    await message.reply_text(
        "Final step: Enter the currency code, for example USD."
    )

    return ADD_CURRENCY


async def add_currency(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_CURRENCY

    currency = message.text.strip().upper()

    if len(currency) != 3 or not currency.isalpha():
        await message.reply_text(
            "Enter a valid three-letter currency code, such as USD."
        )
        return ADD_CURRENCY

    data = context.user_data.get("new_product", {})
    data["currency"] = currency

    pool = _get_pool(context)

    if pool is None:
        await message.reply_text("Database is unavailable.")
        context.user_data.pop("new_product", None)
        return ConversationHandler.END

    try:
        product = await db.create_product(
            pool,
            name=data["name"],
            description=data.get("description", ""),
            category=data["category"],
            price=data["price"],
            currency=data["currency"],
            country=data.get("country", ""),
            features=data.get("features", ""),
            compatibility=data.get("compatibility", ""),
            limits=data.get("limits", ""),
        )

        await message.reply_text(
            "✅ PRODUCT ADDED SUCCESSFULLY!\n\n"
            f"Product ID: {product['id']}\n"
            f"Name: {product['name']}\n"
            f"Category: {product['category']}\n"
            f"Country: {product.get('country') or '—'}\n"
            f"Price: {_format_price(product)}\n\n"
            "The product will appear in its matching store category."
        )

    except Exception:
        logger.exception("Failed to create product")
        await message.reply_text(
            "❌ I couldn't save this product. Please try again."
        )

    context.user_data.pop("new_product", None)

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin: edit product
# ---------------------------------------------------------------------------

async def edit_product_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if not await _require_admin(update):
        return ConversationHandler.END

    message = update.effective_message

    if message is None:
        return ConversationHandler.END

    if not context.args:
        await message.reply_text(
            "Usage: /editproduct ID\nExample: /editproduct 1"
        )
        return ConversationHandler.END

    try:
        product_id = int(context.args[0])
    except ValueError:
        await message.reply_text("Please provide a valid product ID.")
        return ConversationHandler.END

    pool = _get_pool(context)

    if pool is None:
        await message.reply_text("Database is unavailable.")
        return ConversationHandler.END

    product = await db.get_product(pool, product_id)

    if product is None:
        await message.reply_text("Product not found.")
        return ConversationHandler.END

    context.user_data["edit_product"] = {
        "id": product_id,
        "country": product.get("country") or "",
        "features": product.get("features") or "",
        "compatibility": product.get("compatibility") or "",
        "limits": product.get("limits") or "",
        "pricing_options": product.get("pricing_options"),
    }

    await message.reply_text(
        f"✏️ EDIT PRODUCT #{product_id}\n\n"
        f"Current name: {product['name']}\n"
        "Step 1 of 6: Enter the new product name."
    )

    return EDIT_NAME


async def edit_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_NAME

    value = message.text.strip()

    if not value or len(value) > 150:
        await message.reply_text("Enter a valid product name.")
        return EDIT_NAME

    context.user_data["edit_product"]["name"] = value

    await message.reply_text(
        "Step 2 of 6: Enter the new description. Send - for empty."
    )

    return EDIT_DESCRIPTION


async def edit_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_DESCRIPTION

    value = message.text.strip()

    context.user_data["edit_product"]["description"] = (
        "" if value == "-" else value
    )

    await message.reply_text(
        "Step 3 of 6: Enter one official category:\n\n"
        + "\n".join(f"• {item}" for item in CATEGORIES)
    )

    return EDIT_CATEGORY


async def edit_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_CATEGORY

    category = _canonical_category(message.text)

    if category is None:
        await message.reply_text(
            "Please enter one of the five official categories."
        )
        return EDIT_CATEGORY

    data = context.user_data["edit_product"]
    data["category"] = category

    if category != "Wallets & Virtual Cards":
        data["country"] = ""

    await message.reply_text("Step 4 of 6: Enter the new price.")

    return EDIT_PRICE


async def edit_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_PRICE

    price = _parse_price(message.text)

    if price is None:
        await message.reply_text(
            "Enter a valid price, such as 60 or 3.50."
        )
        return EDIT_PRICE

    context.user_data["edit_product"]["price"] = price

    await message.reply_text(
        "Step 5 of 6: Enter the currency code, for example USD."
    )

    return EDIT_CURRENCY


async def edit_currency(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_CURRENCY

    currency = message.text.strip().upper()

    if len(currency) != 3 or not currency.isalpha():
        await message.reply_text(
            "Enter a valid three-letter currency code."
        )
        return EDIT_CURRENCY

    context.user_data["edit_product"]["currency"] = currency

    await message.reply_text(
        "Step 6 of 6: Should this product be active in the shop?\n"
        "Reply YES or NO."
    )

    return EDIT_ACTIVE


async def edit_active(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_ACTIVE

    answer = message.text.strip().lower()

    if answer not in ("yes", "no"):
        await message.reply_text("Please reply YES or NO.")
        return EDIT_ACTIVE

    data = context.user_data.get("edit_product", {})
    pool = _get_pool(context)

    if pool is None:
        await message.reply_text("Database is unavailable.")
        context.user_data.pop("edit_product", None)
        return ConversationHandler.END

    try:
        product = await db.update_product(
            pool,
            data["id"],
            name=data["name"],
            description=data["description"],
            category=data["category"],
            price=data["price"],
            currency=data["currency"],
            active=(answer == "yes"),
            country=data.get("country", ""),
            features=data.get("features", ""),
            compatibility=data.get("compatibility", ""),
            limits=data.get("limits", ""),
            pricing_options=data.get("pricing_options"),
        )

        if product is None:
            await message.reply_text("Product not found.")

        else:
            status = "Active" if product["active"] else "Inactive"

            await message.reply_text(
                "✅ PRODUCT UPDATED!\n\n"
                f"Product ID: {product['id']}\n"
                f"Name: {product['name']}\n"
                f"Category: {product['category']}\n"
                f"Country: {product.get('country') or '—'}\n"
                f"Price: {_format_price(product)}\n"
                f"Status: {status}"
            )

    except Exception:
        logger.exception("Failed to update product")
        await message.reply_text(
            "❌ I couldn't update this product. Please try again."
        )

    context.user_data.pop("edit_product", None)

    return ConversationHandler.END


async def delete_product(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _require_admin(update):
        return

    message = update.effective_message

    if message is None:
        return

    if not context.args:
        await message.reply_text("Usage: /deleteproduct ID")
        return

    try:
        product_id = int(context.args[0])
    except ValueError:
        await message.reply_text("Please provide a valid product ID.")
        return

    pool = _get_pool(context)

    if pool is None:
        await message.reply_text("Database is unavailable.")
        return

    try:
        success = await db.deactivate_product(pool, product_id)

        if success:
            await message.reply_text(
                f"✅ Product #{product_id} has been deactivated."
            )
        else:
            await message.reply_text(
                "Product not found or already inactive."
            )

    except Exception:
        logger.exception("Failed to deactivate product")
        await message.reply_text(
            "❌ I couldn't deactivate this product."
        )


async def cancel_admin_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    context.user_data.pop("new_product", None)
    context.user_data.pop("edit_product", None)

    if message is not None:
        await message.reply_text("Admin operation cancelled.")

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Text and menu handling
# ---------------------------------------------------------------------------

async def echo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or not message.text or user is None:
        return

    text = message.text.strip()

    if text == MENU_HOME:
        await start(update, context)

    elif text == MENU_SHOP:
        await shop(update, context)

    elif text == MENU_ORDERS:
        await my_orders(update, context)

    elif text == MENU_PAYMENT:
        await payment_methods(update, context)

    elif text == MENU_SUPPORT:
        await customer_support(update, context)

    elif text == MENU_HELP:
        await help_command(update, context)

    elif text == MENU_ABOUT:
        await about(update, context)

    elif text == MENU_PING:
        await ping(update, context)

    else:
        target = commands.button_target(text)

        if target is not None:
            handlers = {
                "help": help_command,
                "about": about,
                "ping": ping,
                "start": start,
                "shop": shop,
                "orders": my_orders,
                "payment": payment_methods,
                "support": customer_support,
            }

            if target in handlers:
                await handlers[target](update, context)

            else:
                command = commands.lookup(target)

                if command is None:
                    await message.reply_text(
                        "This button's command is currently unavailable."
                    )
                else:
                    await commands.send(message, command)

            return

        count = _LOCAL_MESSAGE_COUNTS[user.id] = (
            _LOCAL_MESSAGE_COUNTS.get(user.id, 0) + 1
        )

        await message.reply_text(
            f"You sent (#{count}):\n{message.text}",
            reply_markup=_main_menu_keyboard(),
        )


# ---------------------------------------------------------------------------
# Dynamic commands
# ---------------------------------------------------------------------------

def _parse_command_name(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0]
    token = token.lstrip("/")
    token = token.split("@", 1)[0]
    return token.lower()


async def dynamic_command_dispatcher(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None or not message.text:
        return

    command = commands.lookup(
        _parse_command_name(message.text)
    )

    if command is not None:
        await commands.send(message, command)
    else:
        await message.reply_text(
            "Unknown command. Type /help for assistance."
        )


async def dynamic_command_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    query = update.callback_query

    if query is None or query.data is None:
        return

    name = query.data.removeprefix(DYNAMIC_CALLBACK_PREFIX)
    command = commands.lookup(name)

    if command is None:
        await query.answer(
            "This command is no longer available.",
            show_alert=True,
        )
        return

    await query.answer()

    if query.message is not None:
        await commands.send(query.message, command)


# ---------------------------------------------------------------------------
# Error handling and Telegram command menu
# ---------------------------------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error = context.error

    if isinstance(error, (Conflict, NetworkError, TimedOut)):
        logger.warning("Transient Telegram error: %s", error)
        return

    logger.exception(
        "Error while processing update: %s",
        update,
        exc_info=error,
    )

    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Sorry, an error occurred while processing your message."
        )


async def set_bot_commands(application: Application) -> None:
    customer_commands = [
        BotCommand(name, description)
        for name, description in BOT_COMMANDS
    ]

    for name, description in commands.menu_commands():
        if name not in {item.command for item in customer_commands}:
            customer_commands.append(
                BotCommand(name, description or name)
            )

    await application.bot.set_my_commands(customer_commands)

    admin_commands = customer_commands + [
        BotCommand("addproduct", "Add a product"),
        BotCommand("products", "View all products"),
        BotCommand("editproduct", "Edit a product"),
        BotCommand("deleteproduct", "Deactivate a product"),
        BotCommand("cancel", "Cancel an admin operation"),
    ]

    admin_ids = {
        value.strip()
        for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if value.strip()
    }

    for admin_id in admin_ids:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(
                    chat_id=int(admin_id)
                ),
            )
        except (ValueError, TypeError):
            logger.warning(
                "Invalid admin Telegram ID in configuration"
            )


# ---------------------------------------------------------------------------
# Register handlers
# ---------------------------------------------------------------------------

def register_handlers(application: Application) -> None:
    # Customer commands.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("orders", my_orders))
    application.add_handler(CommandHandler("payment", payment_methods))
    application.add_handler(CommandHandler("paysupport", payment_support))
    application.add_handler(CommandHandler("support", customer_support))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("ping", ping))

    # Admin commands.

    application.add_handler(
        CommandHandler("products", admin_products)
    )
    application.add_handler(
        CommandHandler("deleteproduct", delete_product)
    )

    add_product_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addproduct", add_product_start)
        ],
        states={
            ADD_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_name,
                )
            ],
            ADD_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_description,
                )
            ],
            ADD_CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_category,
                )
            ],
            ADD_COUNTRY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_country,
                )
            ],
            ADD_FEATURES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_features,
                )
            ],
            ADD_COMPATIBILITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_compatibility,
                )
            ],
            ADD_LIMITS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_limits,
                )
            ],
            ADD_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_price,
                )
            ],
            ADD_CURRENCY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_currency,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin_action)
        ],
        allow_reentry=True,
    )

    application.add_handler(add_product_conversation)

    edit_product_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("editproduct", edit_product_start)
        ],
        states={
            EDIT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_name,
                )
            ],
            EDIT_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_description,
                )
            ],
            EDIT_CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_category,
                )
            ],
            EDIT_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_price,
                )
            ],
            EDIT_CURRENCY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_currency,
                )
            ],
            EDIT_ACTIVE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_active,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin_action)
        ],
        allow_reentry=True,
    )

    application.add_handler(edit_product_conversation)

    # Catalogue navigation callbacks.
    application.add_handler(
        CallbackQueryHandler(
            category_navigation,
            pattern=r"^nav:(categories|cat:[a-z]+)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            country_navigation,
            pattern=r"^nav:country:\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            product_details,
            pattern=r"^product:\d+$",
        )
    )

    # Customer checkout callback.
    application.add_handler(
        CallbackQueryHandler(
            create_checkout_order,
            pattern=r"^checkout:create:\d+$",
        )
    )

    # Panel-managed command buttons.
    application.add_handler(
        CallbackQueryHandler(
            dynamic_command_button,
            pattern=r"^command:[a-z0-9_]{1,32}$",
        )
    )

    # Other slash commands configured through the admin panel.
    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            dynamic_command_dispatcher,
        )
    )

    # Reply-keyboard buttons and ordinary text.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            echo_message,
        )
    )
