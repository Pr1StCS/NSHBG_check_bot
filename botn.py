import os
import json
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import csv
import datetime
import qrcode
import asyncio
from io import BytesIO
from collections import defaultdict
from dotenv import load_dotenv

# В начале файла, после импортов
import os
import sys

import os
from yookassa import Configuration, Payment

import asyncio
import threading

import asyncio
from yookassa import Payment/

import asyncio
from aiohttp import web
import threading

async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_health_server():
    """Запускает aiohttp сервер для health checks"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Health check server started on port {port}")
    
    # Бесконечный цикл чтобы сервер не закрывался
    while True:
        await asyncio.sleep(3600)

def run_bot():
    """Запускает телеграм бота"""
    print("🤖 Starting Telegram bot...")
    # Запускаем проверку pending платежей
    asyncio.get_event_loop().create_task(check_pending_payments())
    main()

if __name__ == '__main__':
    print("🚀 Starting application...")
    
    # Запускаем health server в asyncio
    asyncio.get_event_loop().create_task(start_health_server())
    
    # Запускаем бота
    run_bot()

def get_admin_ids():
    """
    Безопасно получает список ADMIN_IDS из переменных окружения
    Возвращает список целых чисел
    """
    try:
        # Получаем строку из переменных окружения (из Railway)
        admin_ids_str = os.environ.get('ADMIN_IDS', '')
        
        # Если в Railway не установлено, используем дефолтные значения
        if not admin_ids_str:
            admin_ids_str = '5080055389'  # ваши текущие админы
            print("⚠️ ADMIN_IDS не найдены в переменных окружения, использую дефолтные")
        
        # Преобразуем строку в список целых чисел
        admin_ids = []
        for id_str in admin_ids_str.split(','):
            id_str_clean = id_str.strip()
            if id_str_clean:  # проверяем что строка не пустая
                try:
                    admin_ids.append(int(id_str_clean))
                except ValueError:
                    print(f"⚠️ Некорректный ID админа: {id_str_clean}")
        
        print(f"✅ Загружено ADMIN_IDS: {admin_ids}")
        return admin_ids
        
    except Exception as e:
        print(f"❌ Ошибка при загрузке ADMIN_IDS: {e}")
        # Возвращаем дефолтные значения в случае ошибки
        return [5080055389, 400097852]

def is_admin(user_id):
    """
    Проверяет, является ли пользователь администратором
    """
    try:
        admin_ids = get_admin_ids()
        return user_id in admin_ids
    except Exception as e:
        print(f"❌ Ошибка при проверке прав админа: {e}")
        return False

# Инициализируем ADMIN_IDS при запуске
ADMIN_IDS = get_admin_ids()

async def check_pending_payments():
    """Периодически проверяет статус pending платежей"""
    while True:
        try:
            # Ждем 30 секунд между проверками
            await asyncio.sleep(30)
            
            print("🔍 Проверка pending платежей...")
            
            # Ищем заказы со статусом pending
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                pending_orders = [order for order in reader if order['Статус'] == 'pending']
            
            for order in pending_orders:
                payment_id = order.get('Payment ID', '')
                if payment_id and payment_id != "no_payment_id":
                    try:
                        # Проверяем статус платежа в ЮKassa
                        payment = Payment.find_one(payment_id)
                        
                        if payment.status == 'succeeded':
                            print(f"✅ Платеж подтвержден: {payment_id}")
                            # Обновляем статус заказа
                            update_order_status(order['ID заказа'], "active")
                            # Отправляем билет
                            await send_ticket_after_payment(int(order['ID пользователя']), order['ID заказа'])
                            
                        elif payment.status in ['canceled', 'failed']:
                            print(f"❌ Платеж отменен: {payment_id}")
                            update_order_status(order['ID заказа'], "canceled")
                            
                    except Exception as e:
                        print(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                        
        except Exception as e:
            print(f"❌ Ошибка в check_pending_payments: {e}")

async def check_single_payment(payment_id, order_id, user_id):
    """Проверяет статус конкретного платежа"""
    max_checks = 60  # Проверяем 60 раз (30 минут)
    
    for i in range(max_checks):
        await asyncio.sleep(30)  # Ждем 30 секунд
        
        try:
            payment = Payment.find_one(payment_id)
            
            if payment.status == 'succeeded':
                print(f"✅ Платеж подтвержден: {payment_id}")
                update_order_status(order_id, "active")
                await send_ticket_after_payment(user_id, order_id)
                break
                
            elif payment.status in ['canceled', 'failed']:
                print(f"❌ Платеж отменен: {payment_id}")
                update_order_status(order_id, "canceled")
                break
                
            elif payment.status == 'pending':
                print(f"⏳ Платеж еще в процессе: {payment_id} (проверка {i+1}/{max_checks})")
                
        except Exception as e:
            print(f"❌ Ошибка проверки платежа {payment_id}: {e}")

async def send_ticket_after_payment(user_id, order_id):
    """Отправляет билет пользователю после успешной оплаты"""
    try:
        print(f"🚀 Отправка билета пользователю {user_id}, заказ {order_id}")
        
        # Находим информацию о заказе
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for order in reader:
                if order['ID заказа'] == order_id:
                    order_info = order
                    break
            else:
                print(f"❌ Заказ {order_id} не найден")
                return
        
        # Генерируем QR-код
        qr_code = await generate_qr_code(order_id)
        
        # Простое сообщение без форматирования
        event_name = order_info['Мероприятие']
        event_data = EVENTS.get(event_name, {})
        
        ticket_message = f"""🎉 Оплата прошла успешно!

📋 Ваш электронный билет:
🎭 Мероприятие: {event_name}
📅 Дата: {event_data.get('date', 'Не указано')}
📍 Место: {event_data.get('location', 'Не указано')}
🎟️ Категория: {order_info['Категория']}
🔢 Количество: {order_info['Количество']} шт.
💵 Сумма: {order_info['Сумма']} руб.
🆔 ID заказа: {order_id}

📱 Сохраните этот QR-код! Он потребуется для входа на мероприятие."""
        
        # Отправляем билет пользователю
        app = Application.builder().token(BOT_TOKEN).build()
        if qr_code:
            await app.bot.send_photo(
                chat_id=user_id,
                photo=qr_code,
                caption=ticket_message
            )
            print(f"✅ Билет отправлен пользователю {user_id}")
        else:
            await app.bot.send_message(
                chat_id=user_id,
                text=ticket_message
            )
            print(f"✅ Сообщение отправлено пользователю {user_id} (без QR-кода)")
            
    except Exception as e:
        print(f"❌ Ошибка отправки билета: {e}")

def update_order_status(order_id, status):
    """Обновляет статус заказа в CSV"""
    try:
        # Обновляем структуру файла перед работой
        update_orders_file()
        
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
            fieldnames = reader.fieldnames
        
        order_updated = False
        for order in orders:
            if order['ID заказа'] == order_id:
                order['Статус'] = status
                order_updated = True
                break
        
        if order_updated:
            with open(ORDERS_FILE, 'w', newline='', encoding='utf-8-sig') as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(orders)
                
            print(f"✅ Статус заказа {order_id} обновлен на '{status}'")
        else:
            print(f"❌ Заказ {order_id} не найден для обновления")
            
    except Exception as e:
        print(f"❌ Ошибка обновления статуса: {e}")

# Настройка ЮKassa
YOOKASSA_SHOP_ID = os.environ.get('YOOKASSA_SHOP_ID')
YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY')

if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
    print(f"✅ ЮKassa настроен (Shop ID: {YOOKASSA_SHOP_ID})")
else:
    print("❌ ЮKassa не настроен - проверьте переменные окружения")

# Проверка что мы на Railway
ON_RAILWAY = os.environ.get('RAILWAY_ENVIRONMENT') is not None

if ON_RAILWAY:
    # Создаем необходимые директории
    os.makedirs('data', exist_ok=True)
    os.makedirs('event_photos', exist_ok=True)

load_dotenv()

print("=== ИМПОРТЫ УСПЕШНЫ ===")

# Безопасное получение токена
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Установите переменную окружения BOT_TOKEN")
ADMIN_IDS = os.environ.get('ADMIN_IDS', '').split(',')

# Состояния разговора
SELECTING_EVENT, SELECTING_CATEGORY, SELECTING_QUANTITY, CONFIRMING = range(4)
UPLOADING_PHOTO, CONFIRMING_PHOTO = range(4, 6)

# Файлы данных
# Вместо относительных путей использовать абсолютные
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORDERS_FILE = os.path.join(BASE_DIR, "data", "orders.csv")
EVENTS_FILE = os.path.join(BASE_DIR, "data", "events.json")
PHOTOS_DIR = os.path.join(BASE_DIR, "event_photos")

def init_directories():
    """Создает необходимые директории"""
    os.makedirs('data', exist_ok=True)
    os.makedirs('event_photos', exist_ok=True)

def init_orders_file():
    """Инициализация файла заказов с правильными колонками"""
    if not os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file)
            writer.writerow(["Дата", "ID пользователя", "Имя", "Мероприятие", "Категория", "Количество", "Сумма", "ID заказа", "Статус", "Payment ID"])

def update_orders_file():
    """Обновляет структуру CSV файла если нужно"""
    if not os.path.exists(ORDERS_FILE):
        return init_orders_file()
    
    try:
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            existing_columns = reader.fieldnames
        
        if not existing_columns:
            init_orders_file()
            return
            
        # Проверяем наличие всех необходимых колонок
        required_columns = ["Дата", "ID пользователя", "Имя", "Мероприятие", "Категория", "Количество", "Сумма", "ID заказа", "Статус", "Payment ID"]
        
        if not all(col in existing_columns for col in required_columns):
            print("Обновление структуры файла заказов...")
            
            # Читаем старые заказы
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                old_orders = list(reader)
            
            # Перезаписываем с новой структурой
            with open(ORDERS_FILE, 'w', newline='', encoding='utf-8-sig') as file:
                writer = csv.writer(file)
                writer.writerow(required_columns)
                
                for order in old_orders:
                    writer.writerow([
                        order.get('Дата', ''),
                        order.get('ID пользователя', ''),
                        order.get('Имя', ''),
                        order.get('Мероприятие', ''),
                        order.get('Категория', ''),
                        order.get('Количество', ''),
                        order.get('Сумма', ''),
                        order.get('ID заказа', ''),
                        order.get('Статус', 'active'),
                        order.get('Payment ID', 'no_payment_id')
                    ])
            print("Структура файла обновлена")
            
    except Exception as e:
        print(f"Ошибка при обновлении файла: {e}")
        init_orders_file()
        
def load_events():
    """Загружает мероприятия из JSON файла"""
    try:
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, 'r', encoding='utf-8') as file:
                events_data = json.load(file)
                return normalize_ticket_structure(events_data)
        else:
            return {}
    except Exception as e:
        print(f"Ошибка загрузки мероприятий: {e}")
        return {}

def calculate_dynamic_price(event_name, category, base_price):
    """
    Рассчитывает динамическую цену на основе даты мероприятия
    Возвращает текущую цену и информацию о скидке/надбавке
    """
    if event_name not in EVENTS:
        return base_price, None
    
    event_data = EVENTS[event_name]
    event_date_str = event_data['date']
    
    try:
        # Парсим дату мероприятия
        event_date = datetime.datetime.strptime(event_date_str, "%Y-%m-%d %H:%M")
        current_date = datetime.datetime.now()
        
        # Разница в днях
        days_until_event = (event_date - current_date).days
        
        # Правила динамического ценообразования
        pricing_rules = event_data.get('pricing_rules', {})
        
        # Сначала проверяем правила для конкретной категории
        if category in pricing_rules:
            category_rules = pricing_rules[category]
            for days_threshold, new_price in sorted(category_rules.items(), key=lambda x: int(x[0]), reverse=True):
                if days_until_event <= int(days_threshold):
                    return int(new_price), base_price
        
        # Затем общие правила
        if '_general' in pricing_rules:
            general_rules = pricing_rules['_general']
            for days_threshold, new_price in sorted(general_rules.items(), key=lambda x: int(x[0]), reverse=True):
                if days_until_event <= int(days_threshold):
                    return int(new_price), base_price
        
        # Если правил нет - возвращаем базовую цену
        return base_price, None
            
    except Exception as e:
        print(f"Ошибка расчета динамической цены: {e}")
        return base_price, None

def get_price_info_text(current_price, original_price, category):
    """Формирует текст с информацией о цене"""
    if original_price and original_price != current_price:
        if current_price > original_price:
            return f"🎟️ {category}\n💵 Цена: *{current_price} руб.*\n📈 Повышение цены!"
        else:
            return f"🎟️ {category}\n💵 Цена: *{current_price} руб.*\n🎉 Скидка!"
    else:
        return f"🎟️ {category}\n💵 Цена: *{current_price} руб.*"

def normalize_ticket_structure(events_data):
    """Приводит структуру билетов к единому формату"""
    normalized = {}
    for event_name, event_data in events_data.items():
        normalized[event_name] = event_data.copy()
        normalized_tickets = {}
        
        for category, ticket_info in event_data.get('tickets', {}).items():
            if isinstance(ticket_info, dict):
                normalized_tickets[category] = ticket_info
            else:
                normalized_tickets[category] = {
                    'price': ticket_info,
                    'description': ''
                }
        
        normalized[event_name]['tickets'] = normalized_tickets
        
        if 'pricing_rules' not in normalized[event_name]:
            normalized[event_name]['pricing_rules'] = {}
    
    return normalized

def save_events(events_data):
    """Сохраняет мероприятия в JSON файл"""
    try:
        normalized_events = normalize_ticket_structure(events_data)
        with open(EVENTS_FILE, 'w', encoding='utf-8') as file:
            json.dump(normalized_events, file, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Ошибка сохранения мероприятий: {e}")
        return False

def delete_event_photo(photo_path):
    """Удаляет фото мероприятия"""
    try:
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)
            return True
        return False
    except Exception as e:
        print(f"Ошибка удаления фото: {e}")
        return False

async def save_event_photo(photo_file, event_name):
    """Сохраняет фото мероприятия и возвращает путь к файлу"""
    try:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        
        safe_event_name = "".join(c for c in event_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_event_name = safe_event_name.replace(' ', '_')
        
        file_extension = photo_file.file_path.split('.')[-1] if photo_file.file_path else 'jpg'
        timestamp = int(datetime.datetime.now().timestamp())
        filename = f"{safe_event_name}_{timestamp}.{file_extension}"
        filepath = os.path.join(PHOTOS_DIR, filename)
        
        print(f"DEBUG: Сохранение фото в: {filepath}")
        await photo_file.download_to_drive(filepath)
        
        print(f"✅ Фото сохранено: {filepath}")
        return filepath
    except Exception as e:
        print(f"❌ Ошибка сохранения фото: {e}")
        return None

async def generate_qr_code(order_id: str):
    """Генерирует QR-код для билета"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    
    bot_username = "NSHBG_NCH_Ticket_bot"
    qr_data = f"https://t.me/{bot_username}?start=check_{order_id}"
    
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ===== ОСНОВНЫЕ КОМАНДЫ =====

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы"""
    if context.args and context.args[0].startswith('check_'):
        if not is_admin(update.message.from_user.id):
            await update.message.reply_text("❌ Доступ запрещен. Только администраторы могут проверять билеты.")
            return ConversationHandler.END
        
        try:
            await update.message.delete()
        except:
            pass
        
        order_id = context.args[0][6:]
        return await check_ticket_by_id(update, context, order_id)
    
    context.user_data.clear()
    
    if not EVENTS:
        await update.message.reply_text("🎭 На данный момент мероприятий нет.")
        return ConversationHandler.END
    
    # ВЫВОД СПИСКА МЕРОПРИЯТИЙ С ФОТО
    for event_name, event_data in EVENTS.items():
        event_text = f"🎭 *{event_name}*\n"
        event_text += f"📅 {event_data['date']}\n"
        event_text += f"📍 {event_data['location']}\n"
        
        if event_data.get('description'):
            event_text += f"\n📝 {event_data['description']}\n"
        
        # Показываем фото если есть
        if event_data.get('photo') and os.path.exists(event_data['photo']):
            try:
                with open(event_data['photo'], 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=event_text,
                        parse_mode='Markdown'
                    )
            except Exception as e:
                print(f"Ошибка загрузки фото: {e}")
                await update.message.reply_text(event_text, parse_mode='Markdown')
        else:
            await update.message.reply_text(event_text, parse_mode='Markdown')
        
        await asyncio.sleep(0.5)  # Небольшая задержка между сообщениями
    
    keyboard = [list(EVENTS.keys())]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎪 Добро пожаловать в систему покупки билетов!\n\n"
        "Выберите мероприятие из списка выше:",
        reply_markup=reply_markup
    )
    return SELECTING_EVENT

async def create_yookassa_payment(amount, description, order_id):
    """Создание платежа в ЮKassa"""
    try:
        payment = Payment.create({
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/your_bot"
            },
            "capture": True,
            "description": description,
            "metadata": {
                "order_id": order_id,
                "user_id": order_id.split('_')[0]  # ID пользователя
            }
        })
        
        print(f"✅ Платеж создан: {payment.id}")
        return payment
        
    except Exception as e:
        print(f"❌ Ошибка создания платежа: {e}")
        return None

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка платежа через ЮKassa"""
    user_data = context.user_data
    
    amount = user_data['price'] * user_data['quantity']
    description = f"Билеты: {user_data['event']} - {user_data['category']}"
    order_id = f"{update.message.from_user.id}_{int(datetime.datetime.now().timestamp())}"
    
    # Создаем платеж в ЮKassa
    payment = await create_yookassa_payment(amount, description, order_id)
    
    if payment and payment.confirmation.confirmation_url:
        # Сохраняем заказ как "ожидает оплаты" с payment_id
        order_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user = update.message.from_user
        
        # Обязательно инициализируем файл
        init_orders_file()
        
        with open(ORDERS_FILE, 'a', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file)
            writer.writerow([
                order_date, user.id, user.first_name, 
                user_data['event'], user_data['category'], 
                user_data['quantity'], amount, order_id, 
                "pending", payment.id  # Сохраняем payment_id
            ])
        
        print(f"✅ Заказ сохранен: {order_id}, Payment ID: {payment.id}")
        
        # Отправляем ссылку для оплаты
        await update.message.reply_text(
            f"💳 *Для завершения заказа необходимо оплатить:*\n\n"
            f"💰 Сумма: {amount} руб.\n"
            f"🎭 Мероприятие: {user_data['event']}\n"
            f"🎟️ Категория: {user_data['category']}\n"
            f"🔢 Количество: {user_data['quantity']}\n\n"
            f"[💳 **ОПЛАТИТЬ {amount} РУБ.**]({payment.confirmation.confirmation_url})\n\n"
            f"✅ После оплаты билет придет автоматически в течение 1-2 минут.",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True
        )
        
        # Запускаем проверку статуса этого платежа
        asyncio.create_task(check_single_payment(payment.id, order_id, user.id))
        
    else:
        await update.message.reply_text(
            "❌ Ошибка при создании платежа. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    return ConversationHandler.END

async def check_single_payment(payment_id, order_id, user_id):
    """Проверяет статус конкретного платежа"""
    max_checks = 60  # Проверяем 60 раз (30 минут)
    
    for i in range(max_checks):
        await asyncio.sleep(30)  # Ждем 30 секунд
        
        try:
            payment = Payment.find_one(payment_id)
            
            if payment.status == 'succeeded':
                print(f"✅ Платеж подтвержден: {payment_id}")
                update_order_status(order_id, "active")
                await send_ticket_after_payment(user_id, order_id)
                break
                
            elif payment.status in ['canceled', 'failed']:
                print(f"❌ Платеж отменен: {payment_id}")
                update_order_status(order_id, "canceled")
                break
                
            elif payment.status == 'pending':
                print(f"⏳ Платеж еще в процессе: {payment_id} (проверка {i+1}/{max_checks})")
                
        except Exception as e:
            print(f"❌ Ошибка проверки платежа {payment_id}: {e}")

async def select_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор мероприятия"""
    event_name = update.message.text
    
    if event_name not in EVENTS:
        await update.message.reply_text("Пожалуйста, выберите мероприятие из предложенных:")
        return SELECTING_EVENT
    
    context.user_data['event'] = event_name
    event_data = EVENTS[event_name]
    
    keyboard = [list(event_data['tickets'].keys())]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    event_info = f"🎭 {event_name}\n📅 Дата: {event_data['date']}\n📍 Место: {event_data['location']}"
    
    if event_data.get('description'):
        event_info += f"\n📝 {event_data['description']}"
    
    event_info += "\n\nВыберите категорию билета:"
    
    await update.message.reply_text(
        event_info,
        reply_markup=reply_markup
    )
    return SELECTING_CATEGORY

async def select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории билета"""
    category = update.message.text
    event_name = context.user_data['event']
    event_data = EVENTS[event_name]
    
    if category not in event_data['tickets']:
        await update.message.reply_text("Пожалуйста, выберите категорию из предложенных:")
        return SELECTING_CATEGORY
    
    ticket_info = event_data['tickets'][category]
    
    if isinstance(ticket_info, dict):
        base_price = ticket_info['price']
        description = ticket_info.get('description', '')
    else:
        base_price = ticket_info
        description = ''
    
    current_price, original_price = calculate_dynamic_price(event_name, category, base_price)
    
    context.user_data['category'] = category
    context.user_data['price'] = current_price
    context.user_data['base_price'] = base_price
    context.user_data['ticket_description'] = description
    
    response_text = get_price_info_text(current_price, original_price, category)
    
    if description:
        response_text += f"\n📝 {description}"
    
    if original_price and original_price != current_price:
        if current_price > original_price:
            response_text += f"\n\n📈 *Цена повысилась* из-за приближения даты мероприятия!"
        else:
            response_text += f"\n\n🎉 *Вы приобретаете со скидкой* за раннее бронирование!"
    
    response_text += "\n\nВведите количество билетов:"
    
    await update.message.reply_text(
        response_text,
        parse_mode='Markdown'
    )
    return SELECTING_QUANTITY

async def select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор количества билетов"""
    try:
        quantity = int(update.message.text)
        
        if quantity <= 0:
            await update.message.reply_text("Введите число больше 0:")
            return SELECTING_QUANTITY
        
        if quantity > 10:
            await update.message.reply_text("Максимальное количество билетов - 10. Введите меньшее число:")
            return SELECTING_QUANTITY
        
        context.user_data['quantity'] = quantity
        total = context.user_data['price'] * quantity
        
        event_name = context.user_data['event']
        event_data = EVENTS[event_name]
        
        keyboard = [["✅ Подтвердить", "❌ Отменить"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        order_summary = "📋 Ваш заказ:\n"
        order_summary += f"🎭 Мероприятие: {event_name}\n"
        order_summary += f"📅 Дата: {event_data['date']}\n"
        order_summary += f"📍 Место: {event_data['location']}\n"
        order_summary += f"🎟️ Категория: {context.user_data['category']}\n"
        order_summary += f"🔢 Количество: {quantity}\n"
        order_summary += f"💵 Сумма: {total} руб.\n\n"
        order_summary += f"Подтверждаете заказ?"
        
        await update.message.reply_text(
            order_summary,
            reply_markup=reply_markup
        )
        return CONFIRMING
        
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число:")
        return SELECTING_QUANTITY

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение заказа с переходом к оплате"""
    choice = update.message.text
    
    if choice == "✅ Подтвердить":
        # Сохраняем данные заказа
        event_name = context.user_data['event']
        category = context.user_data['category']
        quantity = context.user_data['quantity']
        price = context.user_data['price']
        total = price * quantity
        
        # Показываем итоги и переходим к оплате
        order_summary = "📋 *Ваш заказ:*\n"
        order_summary += f"🎭 Мероприятие: {event_name}\n"
        order_summary += f"🎟️ Категория: {category}\n"
        order_summary += f"🔢 Количество: {quantity}\n"
        order_summary += f"💵 Сумма: {total} руб.\n\n"
        order_summary += "Для завершения заказа необходимо произвести оплату."
        
        keyboard = [["💳 Перейти к оплате", "❌ Отменить заказ"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            order_summary,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return "PAYMENT"  # Переходим к состоянию оплаты
        
    elif choice == "❌ Отменить":
        await update.message.reply_text(
            "Заказ отменен.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

async def process_payment_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка шага оплаты"""
    if update.message.text == "💳 Перейти к оплате":
        return await process_payment(update, context)
    elif update.message.text == "❌ Отменить заказ":
        await update.message.reply_text(
            "Заказ отменен.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("Пожалуйста, выберите вариант из кнопок:")
        return "PAYMENT"

# ===== АДМИН-ПАНЕЛЬ =====

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для входа в админ-панель"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    keyboard = [
        ["📊 Статистика", "🎭 Управление мероприятиями"],
        ["📈 Отчеты", "🔍 Проверить билет"],
        ["🔙 Выход"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        "⚙️ *Админ-панель*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик админ-меню"""
    # ШАГ 1: Проверяем права внутри функции
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        print(f"DEBUG: Игнорируем сообщение от не-админа {user_id}")
        return
    
    # ШАГ 2: Получаем текст сообщения
    choice = update.message.text
    print(f"DEBUG: Админ {user_id} выбрал: '{choice}'")
    
    # ШАГ 3: Обработка кнопок главного меню
    if choice == "📊 Статистика":
        await show_stats(update, context)
        
    elif choice == "🎭 Управление мероприятиями":
        await manage_events_menu(update, context)
        
    elif choice == "📈 Отчеты":
        await generate_reports(update, context)
        
    elif choice == "🔍 Проверить билет":
        await check_ticket_command(update, context)
        
    elif choice == "🔙 Выход":
        await update.message.reply_text(
            "Выход из админ-панели",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # ШАГ 4: Обработка кнопок подменю
    elif choice == "➕ Добавить мероприятие":
        await add_event_start(update, context)
        
    elif choice == "❌ Удалить мероприятие":
        await delete_event_start(update, context)
        
    elif choice == "✏️ Редактировать билеты":
        await edit_tickets_start(update, context)
        
    elif choice == "🖼️ Управление фото":
        await manage_photos_start(update, context)
        
    elif choice == "🔙 Назад":
        await admin_command(update, context)
    
    else:
        # ШАГ 5: Если текст не распознан как кнопка
        await process_admin_text(update, context)

async def add_ticket_to_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление билета к мероприятию"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    if context.user_data.get('action') == 'add_ticket_to_event':
        print("DEBUG: Уже в процессе добавления билета, игнорируем повторный вызов")
        return
    
    context.user_data['action'] = 'add_ticket_to_event'
    context.user_data['ticket_step'] = 'name'
    
    context.user_data.pop('new_ticket_name', None)
    context.user_data.pop('new_ticket_price', None)
    
    print(f"DEBUG: Начало добавления билета для мероприятия {context.user_data['editing_event']}")
    
    await update.message.reply_text(
        "Введите название новой категории билетов:",
        reply_markup=ReplyKeyboardRemove()
    )

async def edit_tickets_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования билетов"""
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий для редактирования")
        return await manage_events_menu(update, context)
    
    events_list = list(EVENTS.keys())
    keyboard = []
    
    for i in range(0, len(events_list), 2):
        keyboard.append(events_list[i:i+2])
    
    keyboard.append(["🔙 Назад"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Выберите мероприятие для редактирования билетов:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'edit_tickets'

async def delete_ticket_from_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление билета из мероприятия"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    if not event_data['tickets']:
        await update.message.reply_text("❌ Нет билетов для удаления")
        return await edit_tickets_process(update, context)
    
    keyboard = [list(event_data['tickets'].keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Выберите категорию билета для удаления:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'delete_ticket_from_event'

async def manage_pricing_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление правилами ценообразования"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    pricing_rules = event_data.get('pricing_rules', {})
    
    rules_text = f"🎯 *Правила ценообразования для '{event_name}'*\n\n"
    
    if not pricing_rules:
        rules_text += "Правила не настроены.\n\n"
    else:
        if '_general' in pricing_rules:
            rules_text += "*Общие правила:*\n"
            for days, price in sorted(pricing_rules['_general'].items()):
                rules_text += f"• За {days} дней: {price} руб.\n"
            rules_text += "\n"
        
        for category, rules in pricing_rules.items():
            if category != '_general':
                rules_text += f"*{category}:*\n"
                for days, price in sorted(rules.items()):
                    rules_text += f"• За {days} дней: {price} руб.\n"
                rules_text += "\n"
    
    keyboard = [
        ["➕ Добавить правило", "✏️ Изменить правило"],
        ["❌ Удалить правило", "🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        rules_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['action'] = 'manage_pricing'

async def add_pricing_rule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления правила ценообразования"""
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    categories = list(event_data['tickets'].keys()) + ["Общие правила"]
    keyboard = [categories[i:i+2] for i in range(0, len(categories), 2)]
    keyboard.append(["🔙 Назад"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Выберите категорию для добавления правила ценообразования:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'add_pricing_rule'
    context.user_data['pricing_step'] = 'category'

async def delete_pricing_rule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало удаления правила ценообразования"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    pricing_rules = event_data.get('pricing_rules', {})
    
    if not pricing_rules:
        await update.message.reply_text("❌ Нет правил ценообразования для удаления")
        return await manage_pricing_rules(update, context)
    
    rules_list = []
    if '_general' in pricing_rules:
        for days, price in pricing_rules['_general'].items():
            rule_text = f"Общие: за {days} дней - {price} руб."
            rules_list.append(rule_text)
    
    for category, rules in pricing_rules.items():
        if category != '_general':
            for days, price in rules.items():
                rule_text = f"{category}: за {days} дней - {price} руб."
                rules_list.append(rule_text)
    
    if not rules_list:
        await update.message.reply_text("❌ Нет правил ценообразования для удаления")
        return await manage_pricing_rules(update, context)
    
    keyboard = [rules_list[i:i+2] for i in range(0, len(rules_list), 2)]
    keyboard.append(["🔙 Назад"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🗑️ Выберите правило для удаления:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'delete_pricing_rule'
    context.user_data['rules_list'] = rules_list

async def edit_pricing_rule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало изменения правила ценообразования"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    pricing_rules = event_data.get('pricing_rules', {})
    
    if not pricing_rules:
        await update.message.reply_text("❌ Нет правил ценообразования для изменения")
        return await manage_pricing_rules(update, context)
    
    rules_list = []
    rules_data = {}
    
    if '_general' in pricing_rules:
        for days, price in pricing_rules['_general'].items():
            rule_text = f"Общие: за {days} дней - {price} руб."
            rules_list.append(rule_text)
            rules_data[rule_text] = {'category': '_general', 'days': days, 'price': price}
    
    for category, rules in pricing_rules.items():
        if category != '_general':
            for days, price in rules.items():
                rule_text = f"{category}: за {days} дней - {price} руб."
                rules_list.append(rule_text)
                rules_data[rule_text] = {'category': category, 'days': days, 'price': price}
    
    if not rules_list:
        await update.message.reply_text("❌ Нет правил ценообразования для изменения")
        return await manage_pricing_rules(update, context)
    
    keyboard = [rules_list[i:i+2] for i in range(0, len(rules_list), 2)]
    keyboard.append(["🔙 Назад"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "✏️ Выберите правило для изменения:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'edit_pricing_rule'
    context.user_data['rules_list'] = rules_list
    context.user_data['rules_data'] = rules_data

async def change_ticket_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Изменение цены билета"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await edit_tickets_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    if not event_data['tickets']:
        await update.message.reply_text("❌ Нет билетов для редактирования")
        return await edit_tickets_process(update, context)
    
    keyboard = [list(event_data['tickets'].keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Выберите категорию билета для изменения цены:",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'change_ticket_price'

async def edit_tickets_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора мероприятия для редактирования билетов"""
    if 'editing_event' in context.user_data and context.user_data.get('action') == 'edit_tickets':
        event_name = context.user_data['editing_event']
        event_data = EVENTS[event_name]
        
        if event_data['tickets']:
            tickets_text = "\n".join([f"• {cat}: {info['price']} руб." for cat, info in event_data['tickets'].items()])
        else:
            tickets_text = "• Нет билетов"
        
        keyboard = [
            ["➕ Добавить билет", "✏️ Изменить цену"],
            ["🎯 Управление ценами", "❌ Удалить билет"],
            ["🔙 Назад"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        
        await update.message.reply_text(
            f"🎫 Билеты для '{event_name}':\n\n{tickets_text}\n\nВыберите действие:",
            reply_markup=reply_markup
        )
        return
    
    if update.message.text == "🔙 Назад":
        context.user_data.clear()
        return await manage_events_menu(update, context)
    
    event_name = update.message.text
    if event_name not in EVENTS:
        await update.message.reply_text("❌ Пожалуйста, выберите мероприятие из списка:")
        return
    
    context.user_data['editing_event'] = event_name
    context.user_data['action'] = 'edit_tickets'
    
    event_data = EVENTS[event_name]
    
    if event_data['tickets']:
        tickets_text = "\n".join([f"• {cat}: {info['price']} руб." for cat, info in event_data['tickets'].items()])
    else:
        tickets_text = "• Нет билетов"
    
    keyboard = [
        ["➕ Добавить билет", "✏️ Изменить цену"],
        ["🎯 Управление ценами", "❌ Удалить билет"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        f"🎫 Билеты для '{event_name}':\n\n{tickets_text}\n\nВыберите действие:",
        reply_markup=reply_markup
    )

async def manage_events_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления мероприятиями"""
    keyboard = [
        ["➕ Добавить мероприятие", "❌ Удалить мероприятие"],
        ["✏️ Редактировать билеты", "🖼️ Управление фото"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    events_list = "\n".join([f"• {event}" for event in EVENTS.keys()]) if EVENTS else "• Нет мероприятий"
    
    await update.message.reply_text(
        f"🎭 *Управление мероприятиями*\n\nТекущие мероприятия:\n{events_list}\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def manage_photos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало управления фото мероприятий"""
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий для управления фото")
        return await manage_events_menu(update, context)
    
    events_list = list(EVENTS.keys())
    keyboard = []
    
    for i in range(0, len(events_list), 2):
        keyboard.append(events_list[i:i+2])
    
    keyboard.append(["🔙 Назад"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🖼️ *Управление фото мероприятий*\n\nВыберите мероприятие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['action'] = 'manage_photos'

async def show_event_photo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню управления фото для выбранного мероприятия"""
    event_name = update.message.text
    
    if event_name not in EVENTS:
        await update.message.reply_text("❌ Пожалуйста, выберите мероприятие из списка:")
        return
    
    context.user_data['editing_event'] = event_name
    event_data = EVENTS[event_name]
    
    if event_data.get('photo') and os.path.exists(event_data['photo']):
        with open(event_data['photo'], 'rb') as photo_file:
            await update.message.reply_photo(
                photo=photo_file,
                caption=f"🖼️ Текущее фото мероприятия '{event_name}'"
            )
    
    keyboard = [
        ["📤 Загрузить новое фото", "🗑️ Удалить фото"],
        ["🔙 Назад к мероприятиям", "🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    status_text = "✅ Есть фото" if event_data.get('photo') and os.path.exists(event_data['photo']) else "❌ Нет фото"
    
    await update.message.reply_text(
        f"🖼️ *Управление фото для '{event_name}'*\n\n"
        f"Статус: {status_text}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['action'] = 'event_photo_menu'

async def upload_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало загрузки фото"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await manage_photos_start(update, context)
    
    event_name = context.user_data['editing_event']
    print(f"DEBUG: Начало загрузки фото для {event_name}")
    
    await update.message.reply_text(
        f"📤 Пришлите фото для мероприятия '{event_name}' (в виде изображения, не файлом):",
        reply_markup=ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True)
    )
    
    context.user_data['action'] = 'uploading_photo'
    print(f"DEBUG: Установлен action: uploading_photo")

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загруженного фото"""
    print(f"DEBUG: Получено фото, user_id: {update.message.from_user.id}")
    
    # Проверяем права админа внутри функции
    if not is_admin(update.message.from_user.id):
        print(f"DEBUG: Фото от не-админа {update.message.from_user.id} - игнорируем")
        return
    
    # Проверяем что мы в режиме загрузки фото
    if context.user_data.get('action') != 'uploading_photo':
        print(f"DEBUG: Фото получено, но action не uploading_photo: {context.user_data.get('action')}")
        await update.message.reply_text("❌ Сначала выберите 'Загрузить новое фото' в меню управления фото")
        return
    
    if 'editing_event' not in context.user_data:
        print("DEBUG: Нет editing_event в user_data")
        await update.message.reply_text("❌ Ошибка: мероприятие не выбрано")
        return await manage_photos_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    print(f"DEBUG: Загрузка фото для мероприятия: {event_name}")
    
    try:
        # Получаем файл фото
        photo_file = await update.message.photo[-1].get_file()
        print(f"DEBUG: Получен файл фото: {photo_file.file_path}")
        
        # Сохраняем фото
        new_photo_path = await save_event_photo(photo_file, event_name)
        
        if not new_photo_path:
            await update.message.reply_text("❌ Ошибка при сохранении фото")
            return await show_event_photo_menu(update, context)
        
        # Удаляем старое фото если есть
        if event_data.get('photo') and os.path.exists(event_data['photo']):
            try:
                os.remove(event_data['photo'])
                print(f"DEBUG: Удалено старое фото: {event_data['photo']}")
            except Exception as e:
                print(f"DEBUG: Ошибка удаления старого фото: {e}")
        
        # Обновляем данные мероприятия
        event_data['photo'] = new_photo_path
        
        if save_events(EVENTS):
            # Показываем новое фото
            with open(new_photo_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"✅ Фото для мероприятия '{event_name}' успешно обновлено!",
                    reply_markup=ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True)
                )
            print(f"DEBUG: Фото успешно обновлено для {event_name}")
        else:
            await update.message.reply_text("❌ Ошибка при сохранении данных мероприятия")
        
        # Возвращаем в меню управления фото
        context.user_data['action'] = 'event_photo_menu'
        
    except Exception as e:
        print(f"❌ Ошибка загрузки фото: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке фото")

async def delete_event_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление фото мероприятия"""
    if 'editing_event' not in context.user_data:
        await update.message.reply_text("❌ Сначала выберите мероприятие")
        return await manage_photos_start(update, context)
    
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    if not event_data.get('photo') or not os.path.exists(event_data['photo']):
        await update.message.reply_text("❌ У мероприятия нет фото для удаления")
        return await show_event_photo_menu(update, context)
    
    keyboard = [["✅ Да, удалить", "❌ Нет, отменить"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"⚠️ Вы уверены, что хотите удалить фото мероприятия '{event_name}'?",
        reply_markup=reply_markup
    )
    
    context.user_data['action'] = 'confirm_photo_delete'

async def confirm_photo_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления фото"""
    choice = update.message.text
    event_name = context.user_data['editing_event']
    event_data = EVENTS[event_name]
    
    if choice == "✅ Да, удалить":
        if delete_event_photo(event_data.get('photo')):
            event_data['photo'] = None
            if save_events(EVENTS):
                await update.message.reply_text(f"✅ Фото мероприятия '{event_name}' удалено!")
            else:
                await update.message.reply_text("❌ Ошибка при сохранении данных")
        else:
            await update.message.reply_text("❌ Ошибка при удалении фото")
    
    elif choice == "❌ Нет, отменить":
        await update.message.reply_text("❌ Удаление фото отменено")
    
    await show_event_photo_menu(update, context)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику заказов"""
    try:
        if not os.path.exists(ORDERS_FILE):
            await update.message.reply_text("📊 Пока нет данных о заказах")
            return

        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
            
        if not orders:
            await update.message.reply_text("📊 Пока нет данных о заказах")
            return
        
        total_orders = len(orders)
        total_revenue = 0
        total_tickets = 0
        
        event_stats = {}
        for order in orders:
            try:
                amount = int(order.get('Сумма', 0))
                total_revenue += amount
            except (ValueError, TypeError):
                amount = 0
            
            try:
                quantity = int(order.get('Количество', 0))
                total_tickets += quantity
            except (ValueError, TypeError):
                quantity = 0
            
            event = order.get('Мероприятие', 'Неизвестно')
            if event not in event_stats:
                event_stats[event] = {'count': 0, 'revenue': 0, 'tickets': 0}
            
            event_stats[event]['count'] += 1
            event_stats[event]['revenue'] += amount
            event_stats[event]['tickets'] += quantity
        
        stats_text = f"""
📊 *Статистика заказов:*

📈 Всего заказов: {total_orders}
🎟️ Всего билетов: {total_tickets}
💰 Общая выручка: {total_revenue} руб.

*По мероприятиям:*
"""
        for event, stats in event_stats.items():
            stats_text += f"\n🎭 *{event}*\n"
            stats_text += f"   📦 Заказов: {stats['count']}\n"
            stats_text += f"   🎟️ Билетов: {stats['tickets']} шт.\n"
            stats_text += f"   💰 Выручка: {stats['revenue']} руб.\n"
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке статистики: {e}")

async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления мероприятия"""
    context.user_data.clear()
    context.user_data['action'] = 'add_event'
    context.user_data['step'] = 'name'
    
    await update.message.reply_text(
        "🎭 *Создание нового мероприятия*\n\nВведите название мероприятия:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )

async def delete_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало удаления мероприятия"""
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий для удаления")
        return
    
    keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🗑️ *Удаление мероприятия*\n\nВыберите мероприятие для удаления:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['action'] = 'delete_event'

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений в админ-панели"""
    if not is_admin(update.message.from_user.id):
        return
    
    text = update.message.text
    user_data = context.user_data
    
    print(f"DEBUG: Обработка текста '{text}', action: {user_data.get('action')}, step: {user_data.get('step')}")

    if update.message.photo and user_data.get('action') == 'uploading_photo':
        return await handle_photo_upload(update, context)
    
    if text == "🔙 Назад":
        if user_data.get('action') == 'edit_tickets':
            return await manage_events_menu(update, context)
        elif user_data.get('action') in ['add_ticket_to_event', 'change_ticket_price', 'delete_ticket_from_event', 'entering_new_price']:
            user_data.pop('action', None)
            user_data.pop('ticket_step', None)
            user_data.pop('new_ticket_name', None)
            user_data.pop('new_ticket_price', None)
            user_data.pop('changing_ticket', None)
            user_data['action'] = 'edit_tickets'
            return await edit_tickets_process(update, context)
        elif user_data.get('action') == 'delete_event':
            return await manage_events_menu(update, context)
        elif user_data.get('action') in ['manage_pricing', 'delete_pricing_rule', 'edit_pricing_rule', 'editing_pricing_rule']:
            user_data.pop('action', None)
            user_data.pop('rules_list', None)
            user_data.pop('rules_data', None)
            user_data.pop('editing_rule', None)
            user_data.pop('pricing_step', None)
            user_data['action'] = 'edit_tickets'
            return await edit_tickets_process(update, context)
        elif user_data.get('action') == 'add_pricing_rule':
            user_data.pop('action', None)
            user_data.pop('pricing_step', None)
            user_data.pop('pricing_category', None)
            user_data.pop('pricing_days', None)
            return await manage_pricing_rules(update, context)
        elif user_data.get('action') in ['manage_photos', 'event_photo_menu', 'uploading_photo', 'confirm_photo_delete']:
            user_data.pop('action', None)
            user_data.pop('editing_event', None)
            return await manage_events_menu(update, context)
        else:
            return await admin_command(update, context)
    
    if user_data.get('action') == 'edit_tickets' and text in EVENTS:
        return await edit_tickets_process(update, context)
    
    if user_data.get('action') == 'delete_event' and text in EVENTS:
        keyboard = [["✅ Да, удалить", "❌ Нет, отменить"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        event_data = EVENTS[text]
        await update.message.reply_text(
            f"⚠️ Вы уверены, что хотите удалить мероприятие?\n\n"
            f"🎭 {text}\n"
            f"📅 {event_data['date']}\n"
            f"📍 {event_data['location']}\n\n"
            f"Это действие нельзя отменить!",
            reply_markup=reply_markup
        )
        user_data['event_to_delete'] = text
        user_data['action'] = 'confirm_delete'
        return
    
    if user_data.get('action') == 'add_event':
        step = user_data.get('step')
        
        if step == 'name':
            user_data['new_event'] = {'name': text, 'tickets': {}}
            user_data['step'] = 'date'
            await update.message.reply_text("📅 Введите дату и время мероприятия (например: 2024-12-25 19:00):")
            
        elif step == 'date':
            user_data['new_event']['date'] = text
            user_data['step'] = 'location'
            await update.message.reply_text("📍 Введите место проведения:")
            
        elif step == 'location':
            user_data['new_event']['location'] = text
            user_data['step'] = 'description'
            await update.message.reply_text("📝 Введите описание мероприятия (или 'нет' чтобы пропустить):")
            
        elif step == 'description':
            if text.lower() != 'нет':
                user_data['new_event']['description'] = text
            else:
                user_data['new_event']['description'] = ''
            
            user_data['step'] = 'tickets'
            keyboard = [
                ["🎫 Добавить билет"],
                ["✅ Завершить создание"]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                "🎟️ Теперь добавьте билеты к мероприятию.\n\n"
                "Нажмите '🎫 Добавить билет' чтобы добавить новую категорию\n"
                "или '✅ Завершить создание' чтобы закончить:",
                reply_markup=reply_markup
            )
            
        elif step == 'tickets':
            if text == "🎫 Добавить билет":
                user_data['adding_ticket'] = True
                user_data['ticket_step'] = 'name'
                user_data.pop('new_ticket_name', None)
                user_data.pop('new_ticket_price', None)
                await update.message.reply_text(
                    "Введите название новой категории билетов:",
                    reply_markup=ReplyKeyboardRemove()
                )
                
            elif text == "✅ Завершить создание":
                new_event = user_data['new_event']
                EVENTS[new_event['name']] = {
                    'date': new_event['date'],
                    'location': new_event['location'],
                    'description': new_event.get('description', ''),
                    'photo': None,
                    'tickets': new_event.get('tickets', {}),
                    'pricing_rules': {}
                }
                
                if save_events(EVENTS):
                    await update.message.reply_text(f"✅ Мероприятие '{new_event['name']}' успешно создано!")
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении мероприятия")
                
                user_data.clear()
                await admin_command(update, context)
            
            elif user_data.get('adding_ticket'):
                ticket_step = user_data.get('ticket_step')
                
                if ticket_step == 'name':
                    user_data['new_ticket_name'] = text
                    user_data['ticket_step'] = 'price'
                    await update.message.reply_text("💵 Введите цену для этой категории:")
                    
                elif ticket_step == 'price':
                    try:
                        price = int(text)
                        user_data['new_ticket_price'] = price
                        user_data['ticket_step'] = 'description'
                        await update.message.reply_text("📝 Введите описание для этой категории (или 'нет' чтобы пропустить):")
                        
                    except ValueError:
                        await update.message.reply_text("❌ Цена должна быть числом. Введите цену:")
                        
                elif ticket_step == 'description':
                    description = text if text.lower() != 'нет' else ''
                    
                    ticket_name = user_data['new_ticket_name']
                    user_data['new_event']['tickets'][ticket_name] = {
                        'price': user_data['new_ticket_price'],
                        'description': description
                    }
                    
                    user_data.pop('new_ticket_name', None)
                    user_data.pop('new_ticket_price', None)
                    user_data.pop('ticket_step', None)
                    user_data.pop('adding_ticket', None)
                    
                    keyboard = [
                        ["🎫 Добавить билет"],
                        ["✅ Завершить создание"]
                    ]
                    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                    
                    tickets_list = ""
                    if user_data['new_event']['tickets']:
                        tickets_list = "\n\n📋 Уже добавленные билеты:\n"
                        for name, info in user_data['new_event']['tickets'].items():
                            tickets_list += f"• {name}: {info['price']} руб."
                            if info.get('description'):
                                tickets_list += f" - {info['description']}"
                            tickets_list += "\n"
                    
                    await update.message.reply_text(
                        f"✅ Билет '{ticket_name}' добавлен!{tickets_list}\n\n"
                        f"Хотите добавить еще билеты?",
                        reply_markup=reply_markup
                    )
    
    elif user_data.get('action') == 'add_ticket_to_event':
        print(f"DEBUG: Обработка добавления билета, шаг: {user_data.get('ticket_step')}, текст: '{text}'")
        
        ticket_step = user_data.get('ticket_step')
        
        if ticket_step == 'name':
            user_data['new_ticket_name'] = text
            user_data['ticket_step'] = 'price'
            print("DEBUG: Переход к шагу 'price'")
            await update.message.reply_text("💵 Введите цену для этой категории:")
            
        elif ticket_step == 'price':
            try:
                price = int(text)
                user_data['new_ticket_price'] = price
                user_data['ticket_step'] = 'description'
                print("DEBUG: Переход к шагу 'description'")
                await update.message.reply_text("📝 Введите описание для этой категории (или 'нет' чтобы пропустить):")
                
            except ValueError:
                await update.message.reply_text("❌ Цена должна быть числом. Введите цену:")
                
        elif ticket_step == 'description':
            description = text if text.lower() != 'нет' else ''
            
            event_name = user_data['editing_event']
            ticket_name = user_data['new_ticket_name']
            
            EVENTS[event_name]['tickets'][ticket_name] = {
                'price': user_data['new_ticket_price'],
                'description': description
            }
            
            if save_events(EVENTS):
                await update.message.reply_text(f"✅ Билет '{ticket_name}' добавлен к мероприятию '{event_name}'!")
            else:
                await update.message.reply_text("❌ Ошибка при сохранении")
            
            user_data.pop('new_ticket_name', None)
            user_data.pop('new_ticket_price', None)
            user_data.pop('ticket_step', None)
            
            user_data['action'] = 'edit_tickets'
            
            print("DEBUG: Билет добавлен, возврат к редактированию")
            
            await edit_tickets_process(update, context)
    
    elif user_data.get('action') == 'change_ticket_price':
        if text == "🔙 Назад":
            user_data.pop('action', None)
            user_data['action'] = 'edit_tickets'
            return await edit_tickets_process(update, context)
        
        event_name = user_data['editing_event']
        
        if text not in EVENTS[event_name]['tickets']:
            await update.message.reply_text("❌ Пожалуйста, выберите категорию из списка:")
            return
        
        user_data['changing_ticket'] = text
        user_data['action'] = 'entering_new_price'
        
        current_price = EVENTS[event_name]['tickets'][text]['price']
        await update.message.reply_text(
            f"Текущая цена для '{text}': {current_price} руб.\n"
            f"Введите новую цену:",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif user_data.get('action') == 'entering_new_price':
        try:
            new_price = int(text)
            event_name = user_data['editing_event']
            ticket_name = user_data['changing_ticket']
            
            EVENTS[event_name]['tickets'][ticket_name]['price'] = new_price
            
            if save_events(EVENTS):
                await update.message.reply_text(f"✅ Цена для '{ticket_name}' изменена на {new_price} руб.!")
            else:
                await update.message.reply_text("❌ Ошибка при сохранении")
            
            user_data.pop('changing_ticket', None)
            
            user_data['action'] = 'edit_tickets'
            
            await edit_tickets_process(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Цена должна быть числом. Введите новую цену:")
    
    elif user_data.get('action') == 'delete_ticket_from_event':
        if text == "🔙 Назад":
            user_data.pop('action', None)
            user_data['action'] = 'edit_tickets'
            return await edit_tickets_process(update, context)
        
        event_name = user_data['editing_event']
        
        if text not in EVENTS[event_name]['tickets']:
            await update.message.reply_text("❌ Пожалуйста, выберите категорию из списка:")
            return
        
        del EVENTS[event_name]['tickets'][text]
        
        if save_events(EVENTS):
            await update.message.reply_text(f"✅ Билет '{text}' удален из мероприятия '{event_name}'!")
        else:
            await update.message.reply_text("❌ Ошибка при сохранении")
        
        user_data.pop('action', None)
        
        user_data['action'] = 'edit_tickets'
        
        await edit_tickets_process(update, context)
    
    elif user_data.get('action') == 'confirm_delete':
        if text == "✅ Да, удалить":
            event_name = user_data['event_to_delete']
            event_data = EVENTS[event_name]
            
            del EVENTS[event_name]
            
            if save_events(EVENTS):
                await update.message.reply_text(
                    f"✅ Мероприятие успешно удалено!\n\n"
                    f"🎭 {event_name}\n"
                    f"📅 {event_data['date']}\n"
                    f"📍 {event_data['location']}"
                )
            else:
                await update.message.reply_text("❌ Ошибка при сохранении изменений")
        else:
            await update.message.reply_text("❌ Удаление отменено")
        
        user_data.clear()
        await admin_command(update, context)
    
    elif user_data.get('action') == 'check_ticket':
        await check_ticket(update, context)
        user_data.clear()

    elif user_data.get('action') == 'manage_pricing':
        if text == "➕ Добавить правило":
            await add_pricing_rule_start(update, context)
        elif text == "✏️ Изменить правило":
            await edit_pricing_rule_start(update, context)
        elif text == "❌ Удалить правило":
            await delete_pricing_rule_start(update, context)
        elif text == "🔙 Назад":
            user_data.pop('action', None)
            user_data['action'] = 'edit_tickets'
            await edit_tickets_process(update, context)

    elif user_data.get('action') == 'add_pricing_rule':
        pricing_step = user_data.get('pricing_step')
        
        if pricing_step == 'category':
            if text == "🔙 Назад":
                user_data.pop('action', None)
                user_data.pop('pricing_step', None)
                await manage_pricing_rules(update, context)
                return
            
            user_data['pricing_category'] = text
            user_data['pricing_step'] = 'days'
            await update.message.reply_text(
                "Введите количество дней до мероприятия (например: 1, 3, 7, 30):",
                reply_markup=ReplyKeyboardRemove()
            )
        
        elif pricing_step == 'days':
            try:
                days = int(text)
                user_data['pricing_days'] = days
                user_data['pricing_step'] = 'price'
                await update.message.reply_text(
                    "Введите новую фиксированную цену для этого периода:",
                    reply_markup=ReplyKeyboardRemove()
                )
            except ValueError:
                await update.message.reply_text("❌ Введите число дней:")
        
        elif pricing_step == 'price':
            try:
                event_name = user_data['editing_event']
                category = user_data['pricing_category']
                days = user_data['pricing_days']
                
                new_price = int(text)
                
                if event_name not in EVENTS:
                    await update.message.reply_text("❌ Ошибка: мероприятие не найдено")
                    return
                
                if 'pricing_rules' not in EVENTS[event_name]:
                    EVENTS[event_name]['pricing_rules'] = {}
                
                if category == "Общие правила":
                    category_key = '_general'
                else:
                    category_key = category
                
                if category_key not in EVENTS[event_name]['pricing_rules']:
                    EVENTS[event_name]['pricing_rules'][category_key] = {}
                
                EVENTS[event_name]['pricing_rules'][category_key][str(days)] = new_price
                
                if save_events(EVENTS):
                    await update.message.reply_text(f"✅ Правило ценообразования добавлено!")
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении")
                
                user_data.pop('pricing_category', None)
                user_data.pop('pricing_days', None)
                user_data.pop('pricing_step', None)
                user_data.pop('action', None)
                
                await manage_pricing_rules(update, context)
                
            except ValueError:
                await update.message.reply_text("❌ Введите корректную цену (число):")

    elif user_data.get('action') == 'delete_pricing_rule':
        if text == "🔙 Назад":
            user_data.pop('action', None)
            user_data.pop('rules_list', None)
            await manage_pricing_rules(update, context)
            return
        
        if text not in user_data.get('rules_list', []):
            await update.message.reply_text("❌ Пожалуйста, выберите правило из списка:")
            return
        
        rule_text = text
        if rule_text.startswith("Общие:"):
            category = '_general'
            parts = rule_text.split("за ")[1].split(" дней - ")
            days = parts[0]
        else:
            category = rule_text.split(":")[0]
            parts = rule_text.split("за ")[1].split(" дней - ")
            days = parts[0]
        
        event_name = user_data['editing_event']
        if category in EVENTS[event_name].get('pricing_rules', {}) and days in EVENTS[event_name]['pricing_rules'][category]:
            del EVENTS[event_name]['pricing_rules'][category][days]
            if not EVENTS[event_name]['pricing_rules'][category]:
                del EVENTS[event_name]['pricing_rules'][category]
            
            if save_events(EVENTS):
                await update.message.reply_text(f"✅ Правило удалено!")
            else:
                await update.message.reply_text("❌ Ошибка при сохранении")
        
        user_data.pop('rules_list', None)
        user_data.pop('action', None)
        
        await manage_pricing_rules(update, context)

    elif user_data.get('action') == 'edit_pricing_rule':
        if text == "🔙 Назад":
            user_data.pop('action', None)
            user_data.pop('rules_list', None)
            user_data.pop('rules_data', None)
            await manage_pricing_rules(update, context)
            return
        
        if text not in user_data.get('rules_list', []):
            await update.message.reply_text("❌ Пожалуйста, выберите правило из списка:")
            return
        
        rule_data = user_data['rules_data'][text]
        user_data['editing_rule'] = rule_data
        user_data['action'] = 'editing_pricing_rule'
        user_data['pricing_step'] = 'price'
        
        await update.message.reply_text(
            f"✏️ Изменение правила:\n"
            f"Категория: {rule_data['category'] if rule_data['category'] != '_general' else 'Общие правила'}\n"
            f"Дней до мероприятия: {rule_data['days']}\n"
            f"Текущая цена: {rule_data['price']} руб.\n\n"
            f"Введите новую фиксированную цену:",
            reply_markup=ReplyKeyboardRemove()
        )

    elif user_data.get('action') == 'editing_pricing_rule':
        try:
            event_name = user_data['editing_event']
            rule_data = user_data['editing_rule']
            category = rule_data['category']
            days = rule_data['days']
            
            new_price = int(text)
            
            if event_name not in EVENTS:
                await update.message.reply_text("❌ Ошибка: мероприятие не найдено")
                return
            
            if 'pricing_rules' not in EVENTS[event_name]:
                EVENTS[event_name]['pricing_rules'] = {}
            
            if category not in EVENTS[event_name]['pricing_rules']:
                EVENTS[event_name]['pricing_rules'][category] = {}
            
            EVENTS[event_name]['pricing_rules'][category][days] = new_price
            
            if save_events(EVENTS):
                await update.message.reply_text(f"✅ Правило ценообразования обновлено!")
            else:
                await update.message.reply_text("❌ Ошибка при сохранении")
            
            user_data.pop('editing_rule', None)
            user_data.pop('pricing_step', None)
            user_data.pop('action', None)
            user_data.pop('rules_list', None)
            user_data.pop('rules_data', None)
            
            await manage_pricing_rules(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Введите корректную цену (число):")

    elif user_data.get('action') == 'manage_photos':
        if text == "🔙 Назад":
            user_data.pop('action', None)
            return await manage_events_menu(update, context)
        else:
            return await show_event_photo_menu(update, context)

    elif user_data.get('action') == 'event_photo_menu':
        if text == "📤 Загрузить новое фото":
            await upload_photo_start(update, context)
        elif text == "🗑️ Удалить фото":
            await delete_event_photo_handler(update, context)
        elif text == "🔙 Назад к мероприятиям":
            user_data.pop('editing_event', None)
            user_data.pop('action', None)
            return await manage_photos_start(update, context)
        elif text == "🔙 Назад":
            user_data.pop('editing_event', None)
            user_data.pop('action', None)
            return await manage_events_menu(update, context)

    elif user_data.get('action') == 'confirm_photo_delete':
        await confirm_photo_delete(update, context)

async def check_ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки билетов"""
    context.user_data['action'] = 'check_ticket'
    await update.message.reply_text(
        "📱 Введите код билета (ID заказа):",
        reply_markup=ReplyKeyboardRemove()
    )

async def check_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка билета"""
    ticket_code = update.message.text.strip()
    
    try:
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
        
        order_found = None
        for order in orders:
            if order['ID заказа'] == ticket_code:
                order_found = order
                break
        
        if not order_found:
            await update.message.reply_text("❌ Билет не найден")
            return
        
        status = order_found.get('Статус', 'active')
        
        if status == 'used':
            await update.message.reply_text(
                f"⚠️ *Билет уже использован!*\n\n"
                f"🆔 ID: {ticket_code}\n"
                f"👤 Покупатель: {order_found['Имя']}\n"
                f"🎭 Мероприятие: {order_found['Мероприятие']}\n"
                f"🎟️ Категория: {order_found['Категория']}\n"
                f"🕒 Дата покупки: {order_found['Дата']}",
            )
        elif status == 'active':
            await mark_ticket_as_used(ticket_code)
            
            await update.message.reply_text(
                f"✅ *Билет подтвержден!*\n\n"
                f"🆔 ID: {ticket_code}\n"
                f"👤 Покупатель: {order_found['Имя']}\n"
                f"🎭 Мероприятие: {order_found['Мероприятие']}\n"
                f"🎟️ Категория: {order_found['Категория']}\n"
                f"🔢 Количество: {order_found['Количество']} шт.\n"
                f"💵 Сумма: {order_found['Сумма']} руб.\n\n"
                f"✅ Билет отмечен как использованный",
            )
        else:
            await update.message.reply_text(f"❌ Неизвестный статус билета: {status}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при проверке билета: {e}")

async def check_ticket_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Проверка билета по ID (для использования из QR-кода)"""
    try:
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
        
        order_found = None
        for order in orders:
            if order['ID заказа'] == order_id:
                order_found = order
                break
        
        if not order_found:
            await update.message.reply_text("❌ Билет не найден")
            return ConversationHandler.END
        
        status = order_found.get('Статус', 'active')
        
        if status == 'used':
            await update.message.reply_text(
                f"⚠️ Билет уже использован!\n\n"
                f"🆔 ID: {order_id}\n"
                f"👤 Покупатель: {order_found['Имя']}\n"
                f"🎭 Мероприятие: {order_found['Мероприятие']}\n"
                f"🎟️ Категория: {order_found['Категория']}\n"
                f"🕒 Дата покупки: {order_found['Дата']}"
            )
        elif status == 'active':
            await mark_ticket_as_used(order_id)
            
            await update.message.reply_text(
                f"✅ Билет подтвержден!\n\n"
                f"🆔 ID: {order_id}\n"
                f"👤 Покупатель: {order_found['Имя']}\n"
                f"🎭 Мероприятие: {order_found['Мероприятие']}\n"
                f"🎟️ Категория: {order_found['Категория']}\n"
                f"🔢 Количество: {order_found['Количество']} шт.\n"
                f"💵 Сумма: {order_found['Сумма']} руб.\n\n"
                f"✅ Билет отмечен как использованный"
            )
        else:
            await update.message.reply_text(f"❌ Неизвестный статус билета: {status}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при проверке билета: {e}")
    
    return ConversationHandler.END

async def mark_ticket_as_used(order_id: str):
    """Помечает билет как использованный"""
    try:
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
            fieldnames = reader.fieldnames
        
        for order in orders:
            if order['ID заказа'] == order_id:
                order['Статус'] = 'used'
                break
        
        with open(ORDERS_FILE, 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(orders)
            
        return True
    except Exception as e:
        print(f"Ошибка при обновлении статуса билета: {e}")
        return False

async def generate_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация отчетов"""
    try:
        if not os.path.exists(ORDERS_FILE):
            await update.message.reply_text("📈 Пока нет данных для отчета")
            return

        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
        
        if not orders:
            await update.message.reply_text("📈 Пока нет данных для отчета")
            return
        
        report = "📈 *Отчет по заказам*\n\n"
        report += f"Всего заказов: {len(orders)}\n"
        
        daily_stats = defaultdict(lambda: {'count': 0, 'revenue': 0, 'tickets': 0})
        
        for order in orders:
            try:
                date_str = order.get('Дата', '')
                if date_str:
                    date = date_str.split()[0]
                else:
                    date = 'Неизвестно'
                
                amount = int(order.get('Сумма', 0))
                quantity = int(order.get('Количество', 0))
                
                daily_stats[date]['count'] += 1
                daily_stats[date]['revenue'] += amount
                daily_stats[date]['tickets'] += quantity
            except (ValueError, TypeError, IndexError):
                continue
        
        report += "\n*По дням:*\n"
        for date, stats in sorted(daily_stats.items()):
            report += f"📅 {date}: {stats['count']} зак., {stats['tickets']} бил., {stats['revenue']} руб.\n"
        
        await update.message.reply_text(report, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при генерации отчета: {e}")

# ===== БАЗОВЫЕ КОМАНДЫ =====

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Операция отменена",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📋 *Доступные команды:*
/start - Начать покупку билетов
/help - Помощь
/id - Мой ID
/events - Список мероприятий
/cancel - Отмена текущей операции

*Для администраторов:*
/admin - Панель управления
/check - Проверка билетов
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    response = f"🔍 Ваш ID: `{user.id}`"
    await update.message.reply_text(response, parse_mode='Markdown')

async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает мероприятия"""
    if not EVENTS:
        await update.message.reply_text("🎭 На данный момент мероприятий нет.")
        return
    
    for event_name, event_data in EVENTS.items():
        event_text = f"🎭 *{event_name}*\n"
        event_text += f"📅 {event_data['date']}\n"
        event_text += f"📍 {event_data['location']}\n"
        
        if event_data.get('description'):
            event_text += f"\n📝 {event_data['description']}\n"
        
        event_text += "\n💵 *Билеты:*\n"
        for category, ticket_info in event_data['tickets'].items():
            if isinstance(ticket_info, dict):
                price = ticket_info['price']
                desc = ticket_info.get('description', '')
            else:
                price = ticket_info
                desc = ''
                
            event_text += f"• {category}: {price} руб."
            if desc:
                event_text += f" - {desc}"
            event_text += "\n"
        
        # Показываем фото если есть
        if event_data.get('photo') and os.path.exists(event_data['photo']):
            try:
                with open(event_data['photo'], 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=event_text,
                        parse_mode='Markdown'
                    )
            except Exception as e:
                print(f"Ошибка загрузки фото: {e}")
                await update.message.reply_text(event_text, parse_mode='Markdown')
        else:
            await update.message.reply_text(event_text, parse_mode='Markdown')
        
        await asyncio.sleep(0.5)  # Небольшая задержка между сообщениями

def main():
    print("=== ЗАПУСК БОТА ===")
    init_directories()
    update_orders_file()
    
    # Загружаем мероприятия
    global EVENTS
    EVENTS = load_events()
    print(f"✅ Загружено мероприятий: {len(EVENTS)}")
    
    # Получаем актуальный список админов
    admin_ids = get_admin_ids()
    print(f"✅ Админы: {admin_ids}")
    
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler для покупки билетов (ДОЛЖЕН БЫТЬ ПЕРВЫМ)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            SELECTING_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_event)],
            SELECTING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_category)],
            SELECTING_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_quantity)],
            CONFIRMING: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_order)],
            "PAYMENT": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_payment_step)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    
    # Обработчик для фото (УПРОЩЕННЫЙ - без фильтра админов)
    app.add_handler(MessageHandler(
        filters.PHOTO,  # ТОЛЬКО фильтр по фото
        handle_photo_upload
    ))
    
    # Команды
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CommandHandler("check", check_ticket_command))
    
    # Обработчик для админов (УПРОЩЕННЫЙ ФИЛЬТР)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        admin_handler
    ))
    
    # Обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Запускаем фоновую проверку pending платежей
    asyncio.get_event_loop().create_task(check_pending_payments())
    
    print("=== БОТ ЗАПУЩЕН ===")
    app.run_polling()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    print(f"Ошибка: {context.error}")
    try:
        if update and update.message:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте еще раз.")
    except:
        pass

if __name__ == '__main__':
    import threading
    from flask import Flask
    
    # Запускаем бота в фоне
    def run_bot_in_background():
        print("🤖 Starting Telegram bot in background...")
        # Запускаем проверку pending платежей
        asyncio.get_event_loop().create_task(check_pending_payments())
        main()
    
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
    bot_thread.start()
    
    # Создаем простой HTTP сервер для Render health checks
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "Telegram Bot is running!"
    
    @app.route('/health')
    def health():
        return {"status": "ok", "service": "telegram-bot"}
    
    # Получаем порт из переменных окружения Render
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Starting health check server on port {port}")
    
    # Запускаем HTTP сервер (блокирующий вызов)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)











