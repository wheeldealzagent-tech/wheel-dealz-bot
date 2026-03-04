import logging
import os
import re
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "vehicle-auction-data-api-copart-iaai.p.rapidapi.com"

SITE_URL = "https://wheel-dealz-site.netlify.app"
MANAGER = "@DV_delta_void"

user_search_cache = {}


def parse_request(text):
    data = {}
    make_model_match = re.search(
        r'(Toyota|Honda|Ford|BMW|Mercedes|Chevrolet|Hyundai|Kia|Nissan|Audi|Volkswagen|Dodge|Jeep|GMC|Lexus|Subaru|Mazda|Mitsubishi|Volvo|Cadillac|Buick|Lincoln|Infiniti|Acura|Tesla|Ram|Chrysler|Pontiac|Saturn|Genesis)[\s\-]*(\w[\w\s\-]*)',
        text, re.IGNORECASE
    )
    if make_model_match:
        data['make'] = make_model_match.group(1).strip()
        data['model'] = make_model_match.group(2).strip().split('\n')[0].strip()
    year_match = re.search(r'\b(19|20)\d{2}\b', text)
    if year_match:
        data['year'] = year_match.group(0)
    for pattern in [r'\$\s*([\d,]+)', r'([\d,]+)\s*\$']:
        m = re.search(pattern, text)
        if m:
            data['budget'] = int(m.group(1).replace(',', ''))
            break
    if 'copart' in text.lower():
        data['auction'] = 'COPART'
    elif 'iaa' in text.lower():
        data['auction'] = 'IAAI'
    state_m = re.search(r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b', text, re.IGNORECASE)
    if state_m:
        data['state'] = state_m.group(0).upper()
    return data


async def search_vehicles(params):
    url = "https://vehicle-auction-data-api-copart-iaai.p.rapidapi.com/vehicles/search"
    query_parts = [v for k, v in params.items() if k in ('make', 'model', 'year') and v]
    query = ' '.join(query_parts) if query_parts else 'Toyota Camry'
    querystring = {"query": query, "limit": "20"}
    if params.get('auction'):
        querystring['auction'] = params['auction']
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
        items = data if isinstance(data, list) else data.get('results', data.get('data', data.get('vehicles', [])))
        budget = params.get('budget')
        if budget and items:
            filtered = []
            for v in items:
                bid = v.get('currentBid') or v.get('buy_now_price') or v.get('price') or 0
                try:
                    if float(bid) <= budget:
                        filtered.append(v)
                except Exception:
                    filtered.append(v)
            items = filtered if filtered else items
        return items[:3]
    except Exception as e:
        logger.error(f"API error: {e}")
        return []


def build_lot_url(vehicle):
    lot_id = vehicle.get('lotId') or vehicle.get('lot_id') or vehicle.get('id') or vehicle.get('lotNumber', '')
    auction = (vehicle.get('auction') or vehicle.get('auctionName') or vehicle.get('source') or '').upper()
    if 'COPART' in auction:
        return f"https://www.copart.com/lot/{lot_id}"
    elif 'IAA' in auction:
        return f"https://www.iaai.com/VehicleDetail/{lot_id}"
    return f"https://www.copart.com/lot/{lot_id}"


def format_vehicle(vehicle, index):
    make = vehicle.get('make') or vehicle.get('brand') or 'N/A'
    model = vehicle.get('model') or 'N/A'
    year = vehicle.get('year') or vehicle.get('modelYear') or '--'
    engine = vehicle.get('engineSize') or vehicle.get('engine') or vehicle.get('cylinders') or '--'
    auction = vehicle.get('auction') or vehicle.get('auctionName') or '--'
    state = vehicle.get('state') or vehicle.get('location') or '--'
    damage = vehicle.get('primaryDamage') or vehicle.get('damage') or '--'
    odometer = vehicle.get('odometer') or vehicle.get('mileage') or '--'
    bid = vehicle.get('currentBid') or vehicle.get('buy_now_price') or vehicle.get('price') or '--'
    lot_url = build_lot_url(vehicle)
    return (
        f"*{index}. {year} {make} {model}*\n"
        f"Двигун: {engine}\n"
        f"Аукціон: {auction} | Штат: {state}\n"
        f"Пошкодження: {damage}\n"
        f"Пробіг: {odometer} миль\n"
        f"Ставка: ${bid}\n"
        f"[Переглянути лот]({lot_url})"
    )


def get_photo_url(vehicle):
    images = vehicle.get('images')
    if isinstance(images, list) and images:
        return images[0]
    return vehicle.get('imageUrl') or vehicle.get('image') or vehicle.get('thumbnail')


async def send_lots(update, context, vehicles, params):
    chat_id = update.effective_chat.id
    if not vehicles:
        await context.bot.send_message(chat_id=chat_id, text="Не знайдено лотів. Спробуйте змінити параметри на сайті.")
        return
    make = params.get('make', '')
    model = params.get('model', '')
    budget = params.get('budget')
    header = f"Знайдено {len(vehicles)} лот(и) для *{make} {model}*"
    if budget:
        header += f" до *${budget:,}*"
    header += ":"
    await context.bot.send_message(chat_id=chat_id, text=header, parse_mode='Markdown')
    for i, vehicle in enumerate(vehicles, 1):
        text = format_vehicle(vehicle, i)
        photo_url = get_photo_url(vehicle)
        try:
            if photo_url:
                await context.bot.send_photo(chat_id=chat_id, photo=photo_url, caption=text, parse_mode='Markdown')
            else:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        except Exception as e:
            logger.warning(f"Photo error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
    keyboard = [
        [InlineKeyboardButton("Звязатися з менеджером", url=f"https://t.me/{MANAGER.lstrip('@')}")],
        [InlineKeyboardButton("Шукати ще варіанти", callback_data="search_more")]
    ]
    user_search_cache[chat_id] = {'params': params}
    await context.bot.send_message(chat_id=chat_id, text="Оберіть дію:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_message(update, context):
    text = update.message.text or ""
    chat_id = update.effective_chat.id
    is_calculation = any(k in text.lower() for k in ['wheel dealz', 'прорахунок', 'розрахунок', 'підбір', 'wheel-dealz'])
    if not is_calculation:
        keyboard = [[InlineKeyboardButton("Зробити розрахунок", url=SITE_URL)]]
        await update.message.reply_text(
            "Привіт! Я бот *Wheel Dealz* - підбираю авто з аукціонів США.\n\nЩоб знайти авто, зробіть розрахунок на сайті:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    await update.message.reply_text("Шукаю лоти, зачекайте...")
    params = parse_request(text)
    logger.info(f"Parsed: {params}")
    vehicles = await search_vehicles(params)
    await send_lots(update, context, vehicles, params)


async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    if query.data == "search_more":
        cached = user_search_cache.get(chat_id)
        if not cached:
            await query.message.reply_text("Надішліть новий запит.")
            return
        params = dict(cached['params'])
        if params.get('budget'):
            params['budget'] = int(params['budget'] * 1.25)
        await query.message.reply_text("Шукаю більше варіантів...")
        vehicles = await search_vehicles(params)
        await send_lots(update=update, context=context, vehicles=vehicles, params=params)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Wheel Dealz Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
