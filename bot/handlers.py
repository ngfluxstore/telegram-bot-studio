
"""Telegram update handlers for NGFLUX STORE."""

import logging
import os
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

from bot import commands, db


logger = logging.getLogger(__name__)

DB_KEY = "db"

_LOCAL_MESSAGE_COUNTS: dict[int, int] = {}

# Customer commands shown in Telegram's Commands menu.
BOT_COMMANDS = (
    ("start", "Open the main menu"),
    ("shop", "Browse store products"),
    ("orders", "View your previous orders"),
    ("payment", "View payment methods"),
    ("support", "Contact customer support"),
    ("help", "Show help"),
    ("about", "About NGFLUX STORE"),
)

# Customer reply-keyboard buttons.
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

(
    ADD_NAME,
    ADD_DESCRIPTION,
    ADD_CATEGORY,
    ADD_PRICE,
    ADD_CURRENCY,
    EDIT_NAME,
    EDIT_DESCRIPTION,
    EDIT_CATEGORY,
    EDIT_PRICE,
    EDIT_CURRENCY,
    EDIT_ACTIVE,
) = range(11)


# --- General helpers ---------------------------------------------------------

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


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Display one clean, consistent customer menu."""

    rows = [
        [MENU_SHOP, MENU_ORDERS],
        [MENU_PAYMENT, MENU_SUPPORT],
        [MENU_HOME],
    ]

    return ReplyKeyboardMarkup(
        rows,
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


def _dynamic_commands_keyboard() -> InlineKeyboardMarkup | None:
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

    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _format_price(product: dict) -> str:
    return f"{product['currency']} {product['price']:,.2f}"


def _get_pool(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data.get(DB_KEY)


def _parse_price(value: str) -> Decimal | None:
    try:
        price = Decimal(value.strip())

        if not price.is_finite() or price < 0:
            return None

        return price

    except (InvalidOperation, ValueError):
        return None


# --- Start and main menu -----------------------------------------------------

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
            pool, user.id, user.username, user.first_name
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
            "Product management commands:\n"
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
            "We offer digital products, subscriptions, wallets "
            "and virtual cards.\n\n"
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


# --- Customer services -------------------------------------------------------

async def payment_methods(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        "💳 NGFLUX STORE PAYMENT METHODS\n\n"
        "We currently support the following payment options:\n\n"
        "• Binance Pay\n"
        "• USDT\n"
        "• BNB\n"
        "• Litecoin (LTC)\n"
        "• Bybit Pay\n\n"
        "📌 To receive the correct payment details for your order, "
        "please contact our support team.\n\n"
        "We verify payments manually before processing orders.\n\n"
        "Support: @Ngfluxstore",
        reply_markup=_main_menu_keyboard(),
    )


async def customer_support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact NGFLUX Support",
                url="https://t.me/Ngfluxstore",
            )
        ]
    ])

    await message.reply_text(
        "💬 NGFLUX STORE CUSTOMER SUPPORT\n\n"
        "Need help with an order, payment, product or service?\n\n"
        "Our support team is available to assist you.\n\n"
        "Tap the button below to contact us.",
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
            "Your order history is temporarily unavailable. "
            "Please try again later."
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
            "I couldn't retrieve your orders right now. "
            "Please contact @Ngfluxstore for assistance."
        )
        return

    if not orders:
        await message.reply_text(
            "📦 YOUR ORDERS\n\n"
            "You don't have any orders yet.\n\n"
            "Visit Shop Products to explore what's available.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    lines = ["📦 YOUR RECENT ORDERS\n"]

    for order in orders:
        order_number = order["order_number"]
        created_at = order["created_at"]

        lines.append(
            f"\nOrder: {order_number}\n"
            f"Product: {order['product_name']}\n"
            f"Quantity: {order['quantity']}\n"
            f"Total: {order['currency']} "
            f"{order['total_amount']:,.2f}\n"
            f"Status: {order['status']}\n"
            f"Date: {created_at}"
        )

    await message.reply_text(
        "\n".join(lines),
        reply_markup=_main_menu_keyboard(),
    )


# --- Customer product catalogue ---------------------------------------------

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
            "Our product catalogue is temporarily unavailable. "
            "Please try again later."
        )
        return

    products = await db.list_products(pool, active_only=True)

    if not products:
        await message.reply_text(
            "🛍️ NGFLUX STORE PRODUCT CATALOGUE\n\n"
            "There are currently no products available.\n"
            "Please check back soon!\n\n"
            "For assistance, contact @Ngfluxstore.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                f"{product['name']} - {_format_price(product)}",
                callback_data=f"{PRODUCT_CALLBACK_PREFIX}{product['id']}",
            )
        ]
        for product in products
    ]

    await message.reply_text(
        "🛍️ NGFLUX STORE PRODUCT CATALOGUE\n\n"
        "Welcome to our store!\n"
        "Select a product below to view its details.",
        reply_markup=InlineKeyboardMarkup(buttons),
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
        if query.message is not None:
            await query.message.reply_text(
                "The product catalogue is temporarily unavailable."
            )
        return

    try:
        product_id = int(
            query.data.removeprefix(PRODUCT_CALLBACK_PREFIX)
        )
    except ValueError:
        return

    product = await db.get_product(pool, product_id)

    if product is None or not product["active"]:
        await query.edit_message_text(
            "Sorry, this product is no longer available."
        )
        return

    description = product["description"] or "No description available."

    text = (
        f"🛍️ {product['name']}\n\n"
        f"📂 Category: {product['category']}\n"
        f"📝 Description: {description}\n\n"
        f"💰 Price: {_format_price(product)}\n\n"
        "To place an order or ask a question, contact "
        "@Ngfluxstore.\n\n"
        "Thank you for choosing NGFLUX STORE!"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Back to Products",
                callback_data="product:back",
            )
        ]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def product_back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()
    pool = _get_pool(context)

    if pool is None:
        await query.edit_message_text(
            "The product catalogue is temporarily unavailable."
        )
        return

    products = await db.list_products(pool, active_only=True)

    if not products:
        await query.edit_message_text(
            "There are currently no products available."
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                f"{product['name']} - {_format_price(product)}",
                callback_data=f"{PRODUCT_CALLBACK_PREFIX}{product['id']}",
            )
        ]
        for product in products
    ]

    await query.edit_message_text(
        "🛍️ NGFLUX STORE PRODUCT CATALOGUE\n\n"
        "Select a product to view its details.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# --- Admin: list products ----------------------------------------------------

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

    products = await db.list_products(pool, active_only=False)

    if not products:
        await message.reply_text(
            "📦 No products have been added yet.\n\n"
            "Use /addproduct to create your first product."
        )
        return

    lines = ["📦 NGFLUX STORE — PRODUCT MANAGEMENT\n"]

    for product in products[:40]:
        status = "🟢 Active" if product["active"] else "🔴 Inactive"

        lines.append(
            f"\nID: {product['id']}\n"
            f"Name: {product['name']}\n"
            f"Category: {product['category']}\n"
            f"Price: {_format_price(product)}\n"
            f"Status: {status}"
        )

    lines.append(
        "\n\nCommands:\n"
        "/addproduct\n"
        "/editproduct ID\n"
        "/deleteproduct ID"
    )

    await message.reply_text("\n".join(lines))


# --- Admin: add product conversation ---------------------------------------

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
        "➕ ADD NEW PRODUCT\n\n"
        "Step 1 of 5: Enter the product name."
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
        await message.reply_text("Please enter a valid product name.")
        return ADD_NAME

    context.user_data["new_product"]["name"] = name

    await message.reply_text(
        "Step 2 of 5: Enter the product description.\n"
        "Send - if you don't want a description."
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

    if description == "-":
        description = ""

    context.user_data["new_product"]["description"] = description

    await message.reply_text(
        "Step 3 of 5: Enter the product category.\n\n"
        "Examples: Wallets, Virtual Cards, Subscriptions, Discord."
    )

    return ADD_CATEGORY


async def add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return ADD_CATEGORY

    category = message.text.strip()

    if not category or len(category) > 80:
        await message.reply_text("Please enter a valid category.")
        return ADD_CATEGORY

    context.user_data["new_product"]["category"] = category

    await message.reply_text(
        "Step 4 of 5: Enter the product price.\n"
        "Example: 60 or 3.50"
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
            "Invalid price. Enter a valid amount, for example 60 or 3.50."
        )
        return ADD_PRICE

    context.user_data["new_product"]["price"] = price

    await message.reply_text(
        "Step 5 of 5: Enter the currency code.\n"
        "For example: USD"
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
            "Please enter a valid three-letter currency code, such as USD."
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
            description=data["description"],
            category=data["category"],
            price=data["price"],
            currency=data["currency"],
        )

        await message.reply_text(
            "✅ PRODUCT ADDED SUCCESSFULLY!\n\n"
            f"Product ID: {product['id']}\n"
            f"Name: {product['name']}\n"
            f"Category: {product['category']}\n"
            f"Price: {_format_price(product)}\n\n"
            "Customers can now find it using /shop."
        )

    except Exception:
        logger.exception("Failed to create product")
        await message.reply_text(
            "❌ I couldn't save this product. Please try again."
        )

    context.user_data.pop("new_product", None)
    return ConversationHandler.END


# --- Admin: edit product conversation --------------------------------------

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

    context.user_data["edit_product"] = {"id": product_id}

    await message.reply_text(
        f"✏️ EDIT PRODUCT #{product_id}\n\n"
        f"Current name: {product['name']}\n\n"
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

    name = message.text.strip()

    if not name or len(name) > 150:
        await message.reply_text("Please enter a valid product name.")
        return EDIT_NAME

    context.user_data["edit_product"]["name"] = name
    await message.reply_text(
        "Step 2 of 6: Enter the new description.\n"
        "Send - for an empty description."
    )

    return EDIT_DESCRIPTION


async def edit_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_DESCRIPTION

    description = message.text.strip()

    if description == "-":
        description = ""

    context.user_data["edit_product"]["description"] = description
    await message.reply_text("Step 3 of 6: Enter the new category.")

    return EDIT_CATEGORY


async def edit_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message

    if message is None or not message.text:
        return EDIT_CATEGORY

    category = message.text.strip()

    if not category or len(category) > 80:
        await message.reply_text("Please enter a valid category.")
        return EDIT_CATEGORY

    context.user_data["edit_product"]["category"] = category
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
        await message.reply_text("Enter a valid price, such as 60 or 3.50.")
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
            "Enter a valid three-letter currency code, such as USD."
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


# --- Admin: deactivate product ----------------------------------------------

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
        await message.reply_text(
            "Usage: /deleteproduct ID\nExample: /deleteproduct 1"
        )
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
                f"✅ Product #{product_id} has been deactivated.\n"
                "It will no longer appear in the customer catalogue."
            )
        else:
            await message.reply_text(
                "Product not found or already inactive."
            )

    except Exception:
        logger.exception("Failed to deactivate product")
        await message.reply_text(
            "❌ I couldn't deactivate this product. Please try again."
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


# --- Text and menu handling --------------------------------------------------

async def echo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or not message.text or user is None:
        return

    text = message.text.strip()

    # Fixed customer menu buttons.
    if text == MENU_HOME:
        await start(update, context)
        return

    if text == MENU_SHOP:
        await shop(update, context)
        return

    if text == MENU_ORDERS:
        await my_orders(update, context)
        return

    if text == MENU_PAYMENT:
        await payment_methods(update, context)
        return

    if text == MENU_SUPPORT:
        await customer_support(update, context)
        return

    if text == MENU_HELP:
        await help_command(update, context)
        return

    if text == MENU_ABOUT:
        await about(update, context)
        return

    if text == MENU_PING:
        await ping(update, context)
        return

    # Preserve any additional buttons configured through the admin panel.
    target = commands.button_target(text)

    if target is not None:
        if target == "help":
            await help_command(update, context)
        elif target == "about":
            await about(update, context)
        elif target == "ping":
            await ping(update, context)
        elif target == "start":
            await start(update, context)
        elif target == "shop":
            await shop(update, context)
        elif target == "orders":
            await my_orders(update, context)
        elif target == "payment":
            await payment_methods(update, context)
        elif target == "support":
            await customer_support(update, context)
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


# --- Dynamic commands --------------------------------------------------------

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

    command = commands.lookup(_parse_command_name(message.text))

    if command is not None:
        await commands.send(message, command)
        return

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


# --- Error handling ----------------------------------------------------------

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


# --- Telegram command menu ---------------------------------------------------

async def set_bot_commands(application: Application) -> None:
    """Set customer commands globally and admin commands privately."""

    customer_commands = [
        BotCommand(name, description)
        for name, description in BOT_COMMANDS
    ]

    # Keep panel-managed commands available to customers as well.
    for name, description in commands.menu_commands():
        if name not in {item.command for item in customer_commands}:
            customer_commands.append(BotCommand(name, description or name))

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
                scope=BotCommandScopeChat(chat_id=int(admin_id)),
            )
        except (ValueError, TypeError):
            logger.warning("Invalid admin Telegram ID in configuration")


# --- Register handlers -------------------------------------------------------

def register_handlers(application: Application) -> None:
    # Customer commands.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("orders", my_orders))
    application.add_handler(CommandHandler("payment", payment_methods))
    application.add_handler(CommandHandler("support", customer_support))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("ping", ping))

    # Admin commands.
    application.add_handler(CommandHandler("products", admin_products))
    application.add_handler(CommandHandler("deleteproduct", delete_product))

    # Add product conversation.
    add_product_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addproduct", add_product_start),
        ],
        states={
            ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)
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
            ADD_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)
            ],
            ADD_CURRENCY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_currency,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin_action),
        ],
        allow_reentry=True,
    )

    application.add_handler(add_product_conversation)

    # Edit product conversation.
    edit_product_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("editproduct", edit_product_start),
        ],
        states={
            EDIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name)
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_price)
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
            CommandHandler("cancel", cancel_admin_action),
        ],
        allow_reentry=True,
    )

    application.add_handler(edit_product_conversation)

    # Product catalogue buttons.
    application.add_handler(
        CallbackQueryHandler(
            product_back,
            pattern=r"^product:back$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            product_details,
            pattern=r"^product:\d+$",
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

    # All customer reply-keyboard buttons and ordinary text.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            echo_message,
        )
    )
