
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

    # Support both current and older category names.
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
            category, [category]
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
