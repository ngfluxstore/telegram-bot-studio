
"""Dynamic Telegram command registry for NGFLUX STORE."""

import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

from bot import db

logger = logging.getLogger(__name__)

RESERVED_NAMES = {
    "start",
    "shop",
    "orders",
    "payment",
    "support",
    "help",
    "about",
    "ping",
    "products",
    "addproduct",
    "editproduct",
    "deleteproduct",
    "cancel",
}

REPLY_TYPES = {"text", "photo", "document"}

_commands = {}
_button_targets = {}


def _normalise_name(value):
    """Convert command text or a button target to a command name."""
    if value is None:
        return ""

    name = str(value).strip()

    if name.startswith("/"):
        name = name[1:]

    if "@" in name:
        name = name.split("@", 1)[0]

    return name.strip().lower()


async def reload(pool):
    """Reload enabled commands and menu buttons from PostgreSQL."""
    global _commands, _button_targets

    _commands = {}
    _button_targets = {}

    if pool is None:
        logger.warning("Command registry cleared: database unavailable.")
        return

    command_rows = await db.list_commands(pool, enabled_only=True)
    button_rows = await db.list_menu_buttons(pool, enabled_only=True)

    for row in command_rows:
        name = _normalise_name(row.get("name"))

        if not name or name in RESERVED_NAMES:
            continue

        reply_type = row.get("reply_type", "text")

        if reply_type not in REPLY_TYPES:
            logger.warning("Skipping command %s: invalid reply type.", name)
            continue

        _commands[name] = dict(row)

    for row in button_rows:
        label = str(row.get("label") or "").strip()
        target = _normalise_name(row.get("command_name"))

        if label and target:
            _button_targets[label.casefold()] = target

    logger.info(
        "Command registry loaded: %d commands and %d buttons.",
        len(_commands),
        len(_button_targets),
    )


def menu_commands():
    """Return enabled custom commands configured to appear in Telegram's menu."""
    result = []

    for name, item in sorted(_commands.items()):
        if not item.get("show_in_menu", True):
            continue

        description = (
            str(item.get("description") or "").strip()
            or f"Open {name}"
        )

        result.append(
            BotCommand(
                command=name,
                description=description[:256],
            )
        )

    return result


def button_target(text):
    """Resolve a typed button label or /command to its command name."""
    if text is None:
        return None

    value = str(text).strip()

    if not value:
        return None

    target = _button_targets.get(value.casefold())

    if target:
        return target

    name = _normalise_name(value)

    if name in _commands or name in RESERVED_NAMES:
        return name

    return None


def lookup(name):
    """Find an enabled dynamic command by name."""
    name = _normalise_name(name)

    command = _commands.get(name)

    return dict(command) if command else None


def _response_keyboard(layout):
    """Build inline response buttons from the admin panel's saved layout."""
    if not layout:
        return None

    rows = []

    for group in layout:
        buttons = []

        for value in group:
            target = _normalise_name(value)

            if not target:
                continue

            label = str(value).strip()

            buttons.append(
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"command:{target}",
                )
            )

        if buttons:
            rows.append(buttons)

    return InlineKeyboardMarkup(rows) if rows else None


async def send(message, command):
    """Send a dynamic command's configured text, photo or document reply."""
    if not command:
        return None

    reply_type = command.get("reply_type", "text")
    reply_text = str(command.get("reply_text") or "")
    media_url = str(command.get("media_url") or "").strip()

    if reply_type not in REPLY_TYPES:
        await message.reply_text("This command has an invalid reply type.")
        return None

    keyboard = _response_keyboard(command.get("keyboard"))

    if reply_type == "photo":
        if not media_url:
            await message.reply_text(
                reply_text or "The image for this command is unavailable.",
                reply_markup=keyboard,
            )
            return None

        return await message.reply_photo(
            photo=media_url,
            caption=reply_text or None,
            reply_markup=keyboard,
        )

    if reply_type == "document":
        if not media_url:
            await message.reply_text(
                reply_text or "The document for this command is unavailable.",
                reply_markup=keyboard,
            )
            return None

        return await message.reply_document(
            document=media_url,
            caption=reply_text or None,
            reply_markup=keyboard,
        )

    return await message.reply_text(
        reply_text or "This command has no reply configured.",
        reply_markup=keyboard,
    )
