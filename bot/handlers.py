
"""Telegram update handlers for NGFLUX STORE."""

import logging

from telegram import (
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
    MessageHandler,
    filters,
)

from bot import commands, db


logger = logging.getLogger(__name__)

DB_KEY = "db"

_LOCAL_MESSAGE_COUNTS: dict[int, int] = {}

BOT_COMMANDS = (
    ("start", "Show the main menu"),
    ("shop", "Browse NGFLUX STORE products"),
    ("help", "Show help"),
    ("about", "Show bot information"),
    ("ping", "Check bot status"),
)

MENU_HELP = "Help"
MENU_ABOUT = "About"
MENU_PING = "Ping"

HELP_TEXT = """NGFLUX STORE Commands:

/start - Open the main menu
/shop - Browse products
/help - Show help
/about - Store information
/ping - Check bot status"""

DYNAMIC_CALLBACK_PREFIX = "command:"
PRODUCT_CALLBACK_PREFIX = "product:"


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    rows: list[list[str]] = [[MENU_HELP, MENU_ABOUT], [MENU_PING]]
    custom_rows: dict[int, list[str]] = {}

    for button in commands.reply_menu_buttons():
        custom_rows.setdefault(button["row_index"], []).append(button["label"])

    rows.extend(custom_rows[index] for index in sorted(custom_rows))

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose a menu item",
    )


def _dynamic_commands_text() -> str:
    items = commands.menu_commands()

    if not items:
        return ""

    lines = [f"/{name} - {description}" for name, description in items]
    return "\n\nAvailable menu commands:\n" + "\n".join(lines)


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


# --- Start and main menu -----------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    pool = context.bot_data.get(DB_KEY)
    is_new = True

    if pool is not None:
        is_new = await db.upsert_user(
            pool, user.id, user.username, user.first_name
        )

    name = user.first_name if user.first_name else "friend"
    greeting = "Welcome" if is_new else "Welcome back"

    await message.reply_text(
        f"{greeting}, {name}! Welcome to NGFLUX STORE.\n\n"
        "Your Trusted Digital Marketplace.\n\n"
        "Choose a menu button below or type /shop to browse our products.",
        reply_markup=_main_menu_keyboard(),
    )

    dynamic_keyboard = _dynamic_commands_keyboard()

    if dynamic_keyboard is not None:
        await message.reply_text(
            "Choose a command:",
            reply_markup=dynamic_keyboard,
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        HELP_TEXT + _dynamic_commands_text(),
        reply_markup=_dynamic_commands_keyboard(),
    )


async def about(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        "NGFLUX STORE\n"
        "Your Trusted Digital Marketplace.\n\n"
        "We offer digital products, subscriptions, wallets and virtual cards.\n\n"
        "Customer Support: @Ngfluxstore"
    )


async def ping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    message = update.effective_message

    if message is not None:
        await message.reply_text("pong")


async def menu_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if text == MENU_HELP:
        await help_command(update, context)
    elif text == MENU_ABOUT:
        await about(update, context)
    elif text == MENU_PING:
        await ping(update, context)


# --- Product catalogue -------------------------------------------------------

async def shop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show active products from the NGFLUX STORE database."""

    message = update.effective_message

    if message is None:
        return

    pool = context.bot_data.get(DB_KEY)

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
            "For assistance, contact @Ngfluxstore."
        )
        return

    buttons = []

    for product in products:
        buttons.append([
            InlineKeyboardButton(
                f"{product['name']} - {_format_price(product)}",
                callback_data=f"{PRODUCT_CALLBACK_PREFIX}{product['id']}",
            )
        ])

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
    """Display details of a selected product."""

    query = update.callback_query

    if query is None or query.data is None:
        return

    await query.answer()

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await query.message.reply_text(
            "The product catalogue is temporarily unavailable."
        )
        return

    try:
        product_id = int(
            query.data.removeprefix(PRODUCT_CALLBACK_PREFIX)
        )
    except ValueError:
        await query.answer("Invalid product.", show_alert=True)
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

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )


async def product_back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Return the customer to the product catalogue."""

    query = update.callback_query

    if query is None:
        return

    await query.answer()

    pool = context.bot_data.get(DB_KEY)

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


# --- Text and menu handling --------------------------------------------------

async def echo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or not message.text or user is None:
        return

    target = commands.button_target(message.text.strip())

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
        f"You sent (#{count}):\n{message.text}"
    )


# --- Dynamic commands --------------------------------------------------------

def _parse_command_name(text: str) -> str:
    """Extract the bare command name from message text."""

    token = text.strip().split(maxsplit=1)[0]
    token = token.lstrip("/")
    token = token.split("@", 1)[0]

    return token.lower()


async def dynamic_command_dispatcher(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle commands managed through the admin panel."""

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
    """Run a panel-managed command selected from an inline button."""

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
    """Publish built-in and panel-managed commands."""

    menu = list(BOT_COMMANDS) + commands.menu_commands()
    await application.bot.set_my_commands(menu)


# --- Register handlers -------------------------------------------------------

def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("ping", ping))

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

    application.add_handler(
        CallbackQueryHandler(
            dynamic_command_button,
            pattern=r"^command:[a-z0-9_]{1,32}$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            dynamic_command_dispatcher,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(f"^({MENU_HELP}|{MENU_ABOUT}|{MENU_PING})$"),
            menu_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            echo_message,
        )
    )
