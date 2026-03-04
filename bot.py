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

    # Make/Model - BMW X3, BMW X5, Toyota Camry etc
    make_model_match = re.search(
        r'(Toyota|Honda|Ford|BMW|Mercedes|Chevrolet|Hyundai|Kia|Nissan|Audi|Volkswagen|Dodge|Jeep|GMC|Lexus|Subaru|Mazda|Mitsubishi|Volvo|Cadillac|Buick|Lincoln|Infiniti|Acura|Tesla|Ram|Chrysler|Pontiac|Saturn|Genesis)[\s\-]*(\S+(?:\s+\S+)?)',
        text, re.IGNORECASE
    )
    if make_model_match:
        data['make'] = make_model_match.group(1).strip()
        data['model'] = make_model_match.group(2).strip().split('\n')[0].strip()

    # Year
    year_match = re.search(r'\b(19|20)\d{2}\b', text)
    if year_match:
        data['year'] = year_match.group(0)

    # Budget - handles: $5,500 | $5500 | 5500$ | Бюджет: $5,500
    budget_match = re.search(r'\$\s*([\d][\d,\.]*)', text)
    if budget_match:
        raw = budget_match.group(1).replace(',', '').replace('.', '')
        try:
            data['budget'] = int(raw)
        except Exception:
            pass

    # Auction
    if re.search(r'copart', text, re.IGNORECASE):
        data['auction'] = 'COPART'
    elif re.search(r'iaai|iaa\b', text, re.IGNORECASE):
        data['auction'] = 'IAAI'

    # State
    state_m = re.search(
        r'\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b',
        text, re.IGNORECASE
    )
    if state_m:
        data['state'] = state_m.group(0)

    logger.info(f"Parsed from message: {data}")
    return data


async def search_vehicles(params):
    url = "https://vehicle-auction-data-api-copart-iaai.p.rapidapi.com/vehicles/search"

    make = params.get('make', '')
    model = params.get('model', '')
    year = params.get('year', '')

    # Build query - try different combinations for best results
    if make and model:
        query = f"{make} {model}"
    elif make:
        query = make
    else:
        query = 'Toyota Camry'

    querystring = {"query": query, "limit": "50"}
    if params.get('auction'):
        querystring['auction'] = params['auction']

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

    logger.info(f"Searching API: query={query}, params={querystring}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers, params=querystring)
            logger.info(f"API status: {response.status_code}")
            response.raise_for_status()
            data = response.json()

        logger.info(f"API response type: {type(data)}, keys: {list(data.keys()) if isinstance(data, dict) else 'list len=' + str(len(data))}")

        # Handle different response structures
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (data.get('results') or data.get('data') or
                     data.get('vehicles') or data.get('lots') or
                     data.get('items') or [])
        else:
            items = []

        logger.info(f"Total items before filter: {len(items)}")

        budget = params.get('budget')
        if budget and items:
            filtered = []
            for v in items:
                bid_raw = (v.get('currentBid') or v.get('buy_now_price') or
                           v.get('salePrice') or v.get('price') or
                           v.get('currentBidFormatted') or 0)
                try:
                    bid_val = float(str(bid_raw).replace(',', '').replace('$', ''))
                    if bid_val <= budget:
                        filtered.append(v)
                except Exception:
                    filtered.append(v)
            logger.info(f"After budget filter ({budget}): {len(filtered)} items")
            items = filtered if filtered else items

        return items[:3]

    except Exception as e:
        logger.error(f"API error: {e}")
        return []


def build_lot_url(vehicle):
    lot_id = (vehicle.get('lotId') or vehicle.get('lot_id') or
              vehicle.get('id') or vehicle.get('lotNumber') or
              vehicle.get('lot') or '')
    auction = (vehicle.get('auction') or vehicle.get('auctionName') or
               vehicle.get('source') or '').upper()
    if 'COPART' in auction:
        return f"https://www.copart.com/lot/{lot_id}"
    elif 'IAA' in auction:
        return f"https://www.iaai.com/VehicleDetail/{lot_id}"
    return f"https://www.copart.com/lot/{lot_id}"


def format_vehicle(vehicle, index):
    make = vehicle.get('make') or vehicle.get('brand') or vehicle.get('manufacturer') or 'N/A'
    model = vehicle.get('model') or 'N/A'
    year = vehicle.get('year') or vehicle.get('modelYear') or vehicle.get('vehicleYear') or '--'
    engine = (vehicle.get('engineSize') or vehicle.get('engine') or
               vehicle.get('cylinders') or vehicle.get('engineType') or
               vehicle.get('engineCapacity') or '--')
    auction = (vehicle.get('auction') or vehicle.get('auctionName') or
               vehicle.get('source') or '--')
    state = (vehicle.get('state') or vehicle.get('location') or
             vehicle.get('city') or vehicle.get('stateName') or '--')
    damage = (vehicle.get('primaryDamage') or vehicle.get('damage') or
              vehicle.get('damageType') or vehicle.get('lossCodes') or '--')
    odometer = (vehicle.get('odometer') or vehicle.get('mileage') or
                vehicle.get('odometerReading') or '--')
    bid = (vehicle.get('currentBid') or vehicle.get('buy_now_price') or
           vehicle.get('salePrice') or vehicle.get('price') or '--')
    lot_url = build_lot_url(vehicle)

    return (
        f"*{index}. {year} {make} {model}*\n"
        f"\u2699\ufe0f Двигун: {engine}\n"
        f"\U0001f3db Аукціон: {auction} | \U0001f4cd Штат: {state}\n"
        f"\U0001f4a5 Пошкодження: {damage}\n"
        f"\U0001f4cf Пробіг: {odometer} миль\n"
        f"\U0001f4b5 Ставка: ${bid}\n"
        f"\U0001f517 [Переглянути лот]({lot_url})"
    )


def get_photo_url(vehicle):
    images = vehicle.get('images')
    if isinstance(images, list) and images:
        img = images[0]
        return img if isinstance(img, str) else img.get('url') or img.get('src')
    return (vehicle.get('imageUrl') or vehicle.get('image') or
            vehicle.get('thumbnail') or vehicle.get('imageHighRes') or
            vehicle.get('mainImage'))


async def send_lots(update, context, vehicles, params):
    chat_id = update.effective_chat.id

    if not vehicles:
        keyboard = [[InlineKeyboardButton("\U0001f4cb Зробити новий розрахунок", url=SITE_URL)]]
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "\U0001f614 На жаль, не знайдено лотів за вашими параметрами.\n\n"
                "Можливі причини:\n"
                "\u2022 Бюджет занадто низький для цього авто\n"
                "\u2022 Авто зараз немає на аукціоні\n\n"
                "Спробуйте змінити параметри на сайті:"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    make = params.get('make', '')
    model = params.get('model', '')
    budget = params.get('budget')
    header = f"\u2705 Знайдено {len(vehicles)} лот(и) для *{make} {model}*"
    if budget:
        header += f" до *${budget:,}*"
    header += ":"
    await context.bot.send_message(chat_id=chat_id, text=header, parse_mode='Markdown')

    for i, vehicle in enumerate(vehicles, 1):
        text = format_vehicle(vehicle, i)
        photo_url = get_photo_url(vehicle)
        try:
            if photo_url:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=photo_url,
                    caption=text, parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id, text=text,
                    parse_mode='Markdown', disable_web_page_preview=False
                )
        except Exception as e:
            logger.warning(f"Send error: {e}")
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
            except Exception:
                pass

    keyboard = [
        [InlineKeyboardButton("\U0001f468\u200d\U0001f4bc Зв'язатися з менеджером", url=f"https://t.me/{MANAGER.lstrip('@')}")],
        [InlineKeyboardButton("\U0001f504 Шукати ще варіанти", callback_data="search_more")]
    ]
    user_search_cache[chat_id] = {'params': params}
    await context.bot.send_message(
        chat_id=chat_id, text="Оберіть дію:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_message(update, context):
    text = update.message.text or ""
    chat_id = update.effective_chat.id

    is_calculation = any(k in text.lower() for k in [
        'wheel dealz', 'прорахунок', 'розрахунок', 'підбір', 'wheel-dealz',
        'хочу підібрати', 'параметри:'
    ])

    if not is_calculation:
        keyboard = [[InlineKeyboardButton("\U0001f4cb Зробити розрахунок", url=SITE_URL)]]
        await update.message.reply_text(
            "\U0001f44b Привіт! Я бот *Wheel Dealz* — підбираю авто з аукціонів США.\n\n"
            "Щоб я знайшов авто для вас, зробіть розрахунок на нашому сайті:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text("\U0001f50d Шукаю підходящі лоти, зачекайте...")
    params = parse_request(text)
    vehicles = await search_vehicles(params)
    await send_lots(update, context, vehicles, params)


async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "search_more":
        cached = user_search_cache.get(chat_id)
        if not cached:
            await query.message.reply_text("Надішліть новий запит для пошуку.")
            return
        params = dict(cached['params'])
        if params.get('budget'):
            params['budget'] = int(params['budget'] * 1.3)
        await query.message.reply_text("\U0001f504 Шукаю ще варіанти з ширшим бюджетом...")
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
