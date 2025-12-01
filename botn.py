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
from yookassa import Configuration, Payment

# Загружаем переменные окружения
load_dotenv()

print("=== ИМПОРТЫ УСПЕШНЫ ===")

# Инициализируем переменные
EVENTS = {}
BOT_TOKEN = None
ADMIN_IDS_CACHE = None

# Состояния разговора
SELECTING_EVENT, SELECTING_CATEGORY, SELECTING_QUANTITY, CONFIRMING = range(4)
PAYMENT_STEP = 5
ADDING_EXISTING_ORDER = 6

# Файлы данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORDERS_FILE = os.path.join(BASE_DIR, "data", "orders.csv")
EVENTS_FILE = os.path.join(BASE_DIR, "data", "events.json")
PHOTOS_DIR = os.path.join(BASE_DIR, "event_photos")

def get_admin_ids():
    """Безопасно получает список ADMIN_IDS с кешированием"""
    global ADMIN_IDS_CACHE
    
    if ADMIN_IDS_CACHE is not None:
        return ADMIN_IDS_CACHE
    
    try:
        admin_ids_str = os.environ.get('ADMIN_IDS', '5080055389')
        admin_ids = []
        
        for id_str in admin_ids_str.split(','):
            id_str_clean = id_str.strip()
            if id_str_clean:
                try:
                    admin_ids.append(int(id_str_clean))
                except ValueError:
                    print(f"⚠️ Некорректный ID админа: {id_str_clean}")
        
        ADMIN_IDS_CACHE = admin_ids
        print(f"✅ Загружено ADMIN_IDS: {admin_ids}")
        return admin_ids
        
    except Exception as e:
        print(f"❌ Ошибка при загрузке ADMIN_IDS: {e}")
        return [5080055389]

def is_admin(user_id):
    """Проверяет, является ли пользователь администратором"""
    try:
        admin_ids = get_admin_ids()
        return user_id in admin_ids
    except Exception as e:
        print(f"❌ Ошибка при проверке прав админа: {e}")
        return False

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
            
        required_columns = ["Дата", "ID пользователя", "Имя", "Мероприятие", "Категория", "Количество", "Сумма", "ID заказа", "Статус", "Payment ID"]
        
        if not all(col in existing_columns for col in required_columns):
            print("Обновление структуры файла заказов...")
            
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                old_orders = list(reader)
            
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
    global EVENTS
    try:
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, 'r', encoding='utf-8') as file:
                events_data = json.load(file)
                EVENTS = normalize_ticket_structure(events_data)
                return EVENTS
        else:
            EVENTS = {}
            return {}
    except Exception as e:
        print(f"Ошибка загрузки мероприятий: {e}")
        EVENTS = {}
        return {}

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

# ===== ФУНКЦИИ ДЛЯ ДОБАВЛЕНИЯ СУЩЕСТВУЮЩИХ ЗАКАЗОВ =====

def add_existing_order(order_data):
    """Добавляет существующий заказ в базу данных"""
    try:
        # Проверяем обязательные поля
        required_fields = ['Дата', 'ID пользователя', 'Имя', 'Мероприятие', 
                          'Категория', 'Количество', 'Сумма', 'ID заказа']
        
        for field in required_fields:
            if field not in order_data or not order_data[field]:
                print(f"❌ Отсутствует обязательное поле: {field}")
                return False
        
        # Проверяем существует ли уже такой заказ
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for order in reader:
                    if order['ID заказа'] == order_data['ID заказа']:
                        print(f"⚠️ Заказ с ID {order_data['ID заказа']} уже существует")
                        return False
        
        # Добавляем заказ
        init_orders_file()  # Убедимся что файл существует
        
        with open(ORDERS_FILE, 'a', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file)
            writer.writerow([
                order_data.get('Дата', datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                order_data['ID пользователя'],
                order_data['Имя'],
                order_data['Мероприятие'],
                order_data['Категория'],
                order_data['Количество'],
                order_data['Сумма'],
                order_data['ID заказа'],
                order_data.get('Статус', 'active'),
                order_data.get('Payment ID', 'existing_order')
            ])
        
        print(f"✅ Существующий заказ добавлен: {order_data['ID заказа']}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка добавления существующего заказа: {e}")
        return False

def import_orders_from_csv(csv_filepath):
    """Импортирует заказы из CSV файла"""
    try:
        if not os.path.exists(csv_filepath):
            print(f"❌ Файл не найден: {csv_filepath}")
            return False
        
        added_count = 0
        skipped_count = 0
        
        with open(csv_filepath, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                # Преобразуем названия колонок если нужно
                order_data = {
                    'Дата': row.get('Дата', row.get('date', '')),
                    'ID пользователя': row.get('ID пользователя', row.get('user_id', '')),
                    'Имя': row.get('Имя', row.get('name', '')),
                    'Мероприятие': row.get('Мероприятие', row.get('event', '')),
                    'Категория': row.get('Категория', row.get('category', '')),
                    'Количество': row.get('Количество', row.get('quantity', '')),
                    'Сумма': row.get('Сумма', row.get('amount', '')),
                    'ID заказа': row.get('ID заказа', row.get('order_id', '')),
                    'Статус': row.get('Статус', row.get('status', 'active')),
                    'Payment ID': row.get('Payment ID', row.get('payment_id', 'existing_order'))
                }
                
                if add_existing_order(order_data):
                    added_count += 1
                else:
                    skipped_count += 1
        
        print(f"✅ Импорт завершен: добавлено {added_count}, пропущено {skipped_count}")
        return added_count > 0
        
    except Exception as e:
        print(f"❌ Ошибка импорта из CSV: {e}")
        return False

# ===== ОСНОВНЫЕ ФУНКЦИИ БОТА =====

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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы"""
    # Проверка билета по QR-коду
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
    
    # Вывод списка мероприятий с фото
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
        
        await asyncio.sleep(0.5)
    
    keyboard = [list(EVENTS.keys())]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎪 Добро пожаловать в систему покупки билетов!\n\n"
        "Выберите мероприятие из списка выше:",
        reply_markup=reply_markup
    )
    return SELECTING_EVENT

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
    
    # Пока используем базовую цену
    current_price = base_price
    
    context.user_data['category'] = category
    context.user_data['price'] = current_price
    context.user_data['base_price'] = base_price
    context.user_data['ticket_description'] = description
    
    response_text = f"🎟️ {category}\n💵 Цена: *{current_price} руб.*"
    
    if description:
        response_text += f"\n📝 {description}"
    
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
        return PAYMENT_STEP
        
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
        return PAYMENT_STEP

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
                "user_id": order_id.split('_')[0]
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
                "pending", payment.id
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

async def check_pending_payments():
    """Периодически проверяет статус pending платежей"""
    while True:
        try:
            await asyncio.sleep(30)
            print("🔍 Проверка pending платежей...")
            
            if not os.path.exists(ORDERS_FILE):
                continue
            
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                pending_orders = [order for order in reader if order['Статус'] == 'pending']
            
            for order in pending_orders:
                payment_id = order.get('Payment ID', '')
                if payment_id and payment_id != "no_payment_id":
                    try:
                        payment = Payment.find_one(payment_id)
                        
                        if payment.status == 'succeeded':
                            print(f"✅ Платеж подтвержден: {payment_id}")
                            update_order_status(order['ID заказа'], "active")
                            await send_ticket_after_payment(int(order['ID пользователя']), order['ID заказа'])
                            
                        elif payment.status in ['canceled', 'failed']:
                            print(f"❌ Платеж отменен: {payment_id}")
                            update_order_status(order['ID заказа'], "canceled")
                            
                    except Exception as e:
                        print(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                        
        except Exception as e:
            print(f"❌ Ошибка в check_pending_payments: {e}")

# ===== ФУНКЦИИ ДЛЯ ПРОВЕРКИ БИЛЕТОВ =====

async def check_ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки билетов"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    context.user_data['action'] = 'check_ticket'
    await update.message.reply_text(
        "📱 Введите код билета (ID заказа):",
        reply_markup=ReplyKeyboardRemove()
    )

async def check_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка билета"""
    if not is_admin(update.message.from_user.id):
        return
    
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
                parse_mode='Markdown'
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
                parse_mode='Markdown'
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

# ===== АДМИН-ПАНЕЛЬ - ГЛАВНОЕ МЕНЮ =====

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для входа в админ-панель"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    keyboard = [
        ["📊 Статистика", "🎭 Управление мероприятиями"],
        ["📦 Управление заказами", "🔍 Проверить билет"],
        ["🔙 Выход"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        "⚙️ *Админ-панель*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик админ-меню - ТОЛЬКО ГЛАВНОЕ МЕНЮ"""
    if not is_admin(update.message.from_user.id):
        return
    
    choice = update.message.text
    
    if choice == "📊 Статистика":
        await show_stats(update, context)
        
    elif choice == "🎭 Управление мероприятиями":
        await manage_events_menu(update, context)
        
    elif choice == "📦 Управление заказами":
        await manage_orders_menu(update, context)
        
    elif choice == "🔍 Проверить билет":
        await check_ticket_command(update, context)
        
    elif choice == "🔙 Выход":
        await update.message.reply_text(
            "Выход из админ-панели",
            reply_markup=ReplyKeyboardRemove()
        )

# ===== УПРАВЛЕНИЕ МЕРОПРИЯТИЯМИ =====

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

async def events_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для меню управления мероприятиями"""
    if not is_admin(update.message.from_user.id):
        return
    
    choice = update.message.text
    
    if choice == "➕ Добавить мероприятие":
        await add_event_start(update, context)
        
    elif choice == "❌ Удалить мероприятие":
        await delete_event_start(update, context)
        
    elif choice == "✏️ Редактировать билеты":
        await edit_tickets_start(update, context)
        
    elif choice == "🖼️ Управление фото":
        await manage_photos_start(update, context)
        
    elif choice == "🔙 Назад":
        await admin_command(update, context)

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
        return await manage_events_menu(update, context)
    
    keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🗑️ *Удаление мероприятия*\n\nВыберите мероприятие для удаления:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    context.user_data['action'] = 'delete_event'

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

# ===== УПРАВЛЕНИЕ ЗАКАЗАМИ =====

async def manage_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления заказами"""
    keyboard = [
        ["📝 Добавить существующий заказ", "📋 Просмотр всех заказов"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        "📦 *Управление заказами*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def orders_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для меню управления заказами"""
    if not is_admin(update.message.from_user.id):
        return
    
    choice = update.message.text
    
    if choice == "📝 Добавить существующий заказ":
        await add_existing_order_command(update, context)
        
    elif choice == "📋 Просмотр всех заказов":
        await view_all_orders(update, context)
        
    elif choice == "🔙 Назад":
        await admin_command(update, context)

async def add_existing_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для добавления существующего заказа через админ-панель"""
    keyboard = [
        ["📝 Добавить вручную", "📁 Импорт из CSV"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📦 *Добавление существующих заказов*\n\n"
        "Выберите способ добавления:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def add_order_manually_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало ручного добавления заказа"""
    context.user_data.clear()
    context.user_data['action'] = 'add_existing_order'
    context.user_data['step'] = 'event'
    
    # Список мероприятий для выбора
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий. Сначала создайте мероприятие.")
        return await add_existing_order_command(update, context)
    
    keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 *Ручное добавление заказа*\n\n"
        "Выберите мероприятие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ADDING_EXISTING_ORDER

async def process_add_order_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ручного добавления заказа"""
    user_data = context.user_data
    text = update.message.text
    
    if text == "🔙 Назад":
        await add_existing_order_command(update, context)
        return ConversationHandler.END
    
    step = user_data.get('step')
    
    if step == 'event':
        if text not in EVENTS:
            await update.message.reply_text("❌ Пожалуйста, выберите мероприятие из списка:")
            return ADDING_EXISTING_ORDER
        
        user_data['event'] = text
        event_data = EVENTS[text]
        
        keyboard = [list(event_data['tickets'].keys()) + ["🔙 Назад"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Мероприятие: {text}\n"
            f"Выберите категорию билета:",
            reply_markup=reply_markup
        )
        user_data['step'] = 'category'
        return ADDING_EXISTING_ORDER
    
    elif step == 'category':
        if text == "🔙 Назад":
            user_data['step'] = 'event'
            keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "Выберите мероприятие:",
                reply_markup=reply_markup
            )
            return ADDING_EXISTING_ORDER
        
        if text not in EVENTS[user_data['event']]['tickets']:
            await update.message.reply_text("❌ Пожалуйста, выберите категорию из списка:")
            return ADDING_EXISTING_ORDER
        
        user_data['category'] = text
        user_data['step'] = 'user_id'
        
        await update.message.reply_text(
            f"Мероприятие: {user_data['event']}\n"
            f"Категория: {text}\n\n"
            f"Введите ID пользователя (Telegram ID):",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'user_id':
        try:
            user_id = int(text)
            user_data['user_id'] = user_id
            user_data['step'] = 'user_name'
            
            await update.message.reply_text(
                "Введите имя пользователя:",
                reply_markup=ReplyKeyboardRemove()
            )
            return ADDING_EXISTING_ORDER
        except ValueError:
            await update.message.reply_text("❌ ID должен быть числом. Введите ID пользователя:")
            return ADDING_EXISTING_ORDER
    
    elif step == 'user_name':
        user_data['user_name'] = text
        user_data['step'] = 'quantity'
        
        await update.message.reply_text(
            "Введите количество билетов:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'quantity':
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
            user_data['quantity'] = quantity
            user_data['step'] = 'amount'
            
            # Автоматически рассчитываем сумму
            event_name = user_data['event']
            category = user_data['category']
            ticket_price = EVENTS[event_name]['tickets'][category]['price']
            total_amount = ticket_price * quantity
            
            user_data['amount'] = total_amount
            user_data['step'] = 'order_id'
            
            await update.message.reply_text(
                f"Количество: {quantity}\n"
                f"Цена за билет: {ticket_price} руб.\n"
                f"Сумма автоматически рассчитана: {total_amount} руб.\n\n"
                f"Введите ID заказа (уникальный номер):",
                reply_markup=ReplyKeyboardRemove()
            )
            return ADDING_EXISTING_ORDER
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть положительным числом. Введите количество:")
            return ADDING_EXISTING_ORDER
    
    elif step == 'order_id':
        order_id = text.strip()
        
        # Проверяем уникальность ID заказа
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for order in reader:
                    if order['ID заказа'] == order_id:
                        await update.message.reply_text(
                            f"❌ Заказ с ID '{order_id}' уже существует. Введите другой ID:"
                        )
                        return ADDING_EXISTING_ORDER
        
        user_data['order_id'] = order_id
        user_data['step'] = 'confirm'
        
        # Формируем сводку
        summary = f"""
📋 *Сводка заказа:*

🎭 Мероприятие: {user_data['event']}
🎟️ Категория: {user_data['category']}
👤 ID пользователя: {user_data['user_id']}
👤 Имя: {user_data['user_name']}
🔢 Количество: {user_data['quantity']}
💵 Сумма: {user_data['amount']} руб.
🆔 ID заказа: {order_id}
📅 Дата: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

✅ Подтвердить добавление заказа?
        """
        
        keyboard = [["✅ Подтвердить", "❌ Отменить"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            summary,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'confirm':
        if text == "✅ Подтвердить":
            # Создаем объект заказа
            order_data = {
                'Дата': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'ID пользователя': user_data['user_id'],
                'Имя': user_data['user_name'],
                'Мероприятие': user_data['event'],
                'Категория': user_data['category'],
                'Количество': user_data['quantity'],
                'Сумма': user_data['amount'],
                'ID заказа': user_data['order_id'],
                'Статус': 'active',
                'Payment ID': 'existing_order'
            }
            
            # Добавляем заказ
            if add_existing_order(order_data):
                # Генерируем QR-код
                qr_code = await generate_qr_code(user_data['order_id'])
                
                # Отправляем билет пользователю
                try:
                    ticket_message = f"""🎉 Ваш билет добавлен в систему!

📋 Электронный билет:
🎭 Мероприятие: {user_data['event']}
📅 Дата: {EVENTS[user_data['event']]['date']}
📍 Место: {EVENTS[user_data['event']]['location']}
🎟️ Категория: {user_data['category']}
🔢 Количество: {user_data['quantity']} шт.
💵 Сумма: {user_data['amount']} руб.
🆔 ID заказа: {user_data['order_id']}

📱 Сохраните этот QR-код! Он потребуется для входа на мероприятие."""
                    
                    if qr_code:
                        await context.bot.send_photo(
                            chat_id=user_data['user_id'],
                            photo=qr_code,
                            caption=ticket_message
                        )
                    
                    # Отправляем подтверждение админу
                    await update.message.reply_text(
                        f"✅ Заказ успешно добавлен!\n"
                        f"Билет отправлен пользователю {user_data['user_name']}",
                        reply_markup=ReplyKeyboardRemove()
                    )
                except Exception as e:
                    print(f"Ошибка отправки билета: {e}")
                    await update.message.reply_text(
                        f"✅ Заказ добавлен, но не удалось отправить билет пользователю: {e}",
                        reply_markup=ReplyKeyboardRemove()
                    )
            else:
                await update.message.reply_text(
                    "❌ Ошибка при добавлении заказа",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            await update.message.reply_text(
                "❌ Добавление заказа отменено",
                reply_markup=ReplyKeyboardRemove()
            )
        
        context.user_data.clear()
        return ConversationHandler.END

async def import_csv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало импорта из CSV"""
    await update.message.reply_text(
        "📁 *Импорт из CSV*\n\n"
        "Для импорта заказов:\n"
        "1. Подготовьте CSV файл со следующими колонками:\n"
        "   - Дата (формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС)\n"
        "   - ID пользователя (Telegram ID)\n"
        "   - Имя\n"
        "   - Мероприятие\n"
        "   - Категория\n"
        "   - Количество\n"
        "   - Сумма\n"
        "   - ID заказа\n"
        "   - Статус (опционально, по умолчанию 'active')\n"
        "   - Payment ID (опционально, по умолчанию 'existing_order')\n\n"
        "2. Отправьте файл в этот чат",
        parse_mode='Markdown'
    )
    context.user_data['action'] = 'import_csv'

async def handle_csv_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загрузки CSV файла"""
    if not is_admin(update.message.from_user.id):
        return
    
    if context.user_data.get('action') != 'import_csv':
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ Пожалуйста, отправьте CSV файл")
        return
    
    file = await update.message.document.get_file()
    filename = update.message.document.file_name
    
    if not filename.lower().endswith('.csv'):
        await update.message.reply_text("❌ Пожалуйста, отправьте файл в формате CSV")
        return
    
    # Скачиваем файл
    temp_path = os.path.join(BASE_DIR, "temp_import.csv")
    await file.download_to_drive(temp_path)
    
    # Импортируем заказы
    await update.message.reply_text("🔄 Импорт заказов...")
    
    success = import_orders_from_csv(temp_path)
    
    # Удаляем временный файл
    if os.path.exists(temp_path):
        os.remove(temp_path)
    
    if success:
        await update.message.reply_text("✅ Импорт успешно завершен!")
    else:
        await update.message.reply_text("❌ Ошибка при импорте. Проверьте формат файла.")
    
    context.user_data.clear()
    await admin_command(update, context)

async def view_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр всех заказов"""
    try:
        if not os.path.exists(ORDERS_FILE):
            await update.message.reply_text("📦 Нет заказов")
            return
        
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
        
        if not orders:
            await update.message.reply_text("📦 Нет заказов")
            return
        
        message = "📋 *Все заказы:*\n\n"
        
        for i, order in enumerate(orders, 1):
            message += f"{i}. 🆔 {order['ID заказа']}\n"
            message += f"   👤 {order['Имя']} (ID: {order['ID пользователя']})\n"
            message += f"   🎭 {order['Мероприятие']} - {order['Категория']}\n"
            message += f"   🔢 {order['Количество']} шт. | 💵 {order['Сумма']} руб.\n"
            message += f"   📅 {order['Дата']} | Статус: {order['Статус']}\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке заказов: {e}")

# ===== ОБРАБОТЧИКИ ТЕКСТОВЫХ СООБЩЕНИЙ =====

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений в админ-панели (для сложных операций)"""
    if not is_admin(update.message.from_user.id):
        return
    
    text = update.message.text
    user_data = context.user_data
    
    print(f"DEBUG process_admin_text: Текст '{text}', action: {user_data.get('action')}")
    
    # Обработка проверки билетов
    if user_data.get('action') == 'check_ticket':
        await check_ticket(update, context)
        user_data.clear()
        return
    
    # Навигация
    if text == "🔙 Назад":
        if user_data.get('action') == 'add_event':
            await manage_events_menu(update, context)
        elif user_data.get('action') == 'delete_event':
            await manage_events_menu(update, context)
        elif user_data.get('action') == 'edit_tickets':
            await manage_events_menu(update, context)
        elif user_data.get('action') == 'manage_photos':
            await manage_events_menu(update, context)
        elif user_data.get('action') == 'manage_orders':
            await admin_command(update, context)
        elif user_data.get('action') == 'import_csv':
            await add_existing_order_command(update, context)
        else:
            await admin_command(update, context)
        user_data.clear()
        return
    
    # Обработка добавления мероприятия (пошагово)
    if user_data.get('action') == 'add_event':
        await process_add_event_steps(update, context)
        return
    
    # Обработка удаления мероприятия
    if user_data.get('action') == 'delete_event':
        if text in EVENTS:
            await confirm_delete_event(update, context, text)
        else:
            await update.message.reply_text("❌ Пожалуйста, выберите мероприятие из списка:")
        return
    
    # Обработка других действий...
    if text == "📝 Добавить вручную":
        return await add_order_manually_start(update, context)
    
    if text == "📁 Импорт из CSV":
        return await import_csv_start(update, context)
    
    if text == "📋 Просмотр всех заказов":
        await view_all_orders(update, context)
        return

async def process_add_event_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пошаговая обработка добавления мероприятия"""
    user_data = context.user_data
    text = update.message.text
    step = user_data.get('step')
    
    if step == 'name':
        user_data['event_name'] = text
        user_data['step'] = 'date'
        
        await update.message.reply_text(
            "📅 Введите дату и время мероприятия (например: 2024-12-25 19:00):",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'date':
        user_data['event_date'] = text
        user_data['step'] = 'location'
        
        await update.message.reply_text(
            "📍 Введите место проведения мероприятия:",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'location':
        user_data['event_location'] = text
        user_data['step'] = 'description'
        
        await update.message.reply_text(
            "📝 Введите описание мероприятия (или 'нет' чтобы пропустить):",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'description':
        if text.lower() == 'нет':
            user_data['event_description'] = ''
        else:
            user_data['event_description'] = text
        
        user_data['step'] = 'tickets'
        user_data['event_tickets'] = {}
        
        await update.message.reply_text(
            "🎟️ Теперь добавьте билеты.\n"
            "Введите название категории билета (например: Стандарт) или 'готово' чтобы завершить:",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'tickets':
        if text.lower() == 'готово':
            # Создаем мероприятие
            event_name = user_data['event_name']
            
            EVENTS[event_name] = {
                'date': user_data['event_date'],
                'location': user_data['event_location'],
                'description': user_data.get('event_description', ''),
                'photo': None,
                'tickets': user_data.get('event_tickets', {}),
                'pricing_rules': {}
            }
            
            if save_events(EVENTS):
                await update.message.reply_text(
                    f"✅ Мероприятие '{event_name}' успешно создано!",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text(
                    "❌ Ошибка при сохранении мероприятия",
                    reply_markup=ReplyKeyboardRemove()
                )
            
            user_data.clear()
            await admin_command(update, context)
        else:
            # Начинаем добавление билета
            user_data['current_ticket_name'] = text
            user_data['step'] = 'ticket_price'
            
            await update.message.reply_text(
                f"💵 Введите цену для категории '{text}' (в рублях):",
                reply_markup=ReplyKeyboardRemove()
            )
            
    elif step == 'ticket_price':
        try:
            price = int(text)
            if price <= 0:
                raise ValueError
            
            ticket_name = user_data['current_ticket_name']
            user_data['event_tickets'][ticket_name] = {
                'price': price,
                'description': ''
            }
            
            user_data['step'] = 'ticket_description'
            
            await update.message.reply_text(
                f"📝 Введите описание для категории '{ticket_name}' (или 'нет' чтобы пропустить):",
                reply_markup=ReplyKeyboardRemove()
            )
            
        except ValueError:
            await update.message.reply_text(
                "❌ Цена должна быть положительным числом. Введите цену:",
                reply_markup=ReplyKeyboardRemove()
            )
            
    elif step == 'ticket_description':
        ticket_name = user_data['current_ticket_name']
        
        if text.lower() == 'нет':
            description = ''
        else:
            description = text
        
        user_data['event_tickets'][ticket_name]['description'] = description
        
        # Сбрасываем текущий билет
        user_data.pop('current_ticket_name', None)
        user_data['step'] = 'tickets'
        
        # Показываем текущий список билетов
        tickets_list = ""
        for name, info in user_data['event_tickets'].items():
            tickets_list += f"• {name}: {info['price']} руб."
            if info['description']:
                tickets_list += f" - {info['description']}"
            tickets_list += "\n"
        
        await update.message.reply_text(
            f"✅ Билет '{ticket_name}' добавлен!\n\n"
            f"Текущие билеты:\n{tickets_list}\n\n"
            "Введите название следующей категории билета или 'готово' чтобы завершить:",
            reply_markup=ReplyKeyboardRemove()
        )

async def confirm_delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE, event_name: str):
    """Подтверждение удаления мероприятия"""
    event_data = EVENTS[event_name]
    context.user_data['event_to_delete'] = event_name
    
    keyboard = [["✅ Да, удалить", "❌ Нет, отменить"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"⚠️ *Вы уверены, что хотите удалить мероприятие?*\n\n"
        f"🎭 *{event_name}*\n"
        f"📅 {event_data['date']}\n"
        f"📍 {event_data['location']}\n\n"
        f"*Внимание:* Это действие нельзя отменить!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ===== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ =====

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

# ===== ОСНОВНЫЕ КОМАНДЫ =====

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
        
        await asyncio.sleep(0.5)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Операция отменена",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    print(f"Ошибка: {context.error}")
    try:
        if update and update.message:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте еще раз.")
    except:
        pass

# ===== ГЛАВНАЯ ФУНКЦИЯ =====

def main():
    print("=== ЗАПУСК БОТА ===")
    
    # Инициализация
    global BOT_TOKEN
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не найден! Установите переменную окружения BOT_TOKEN")
    
    init_directories()
    update_orders_file()
    
    # Загружаем мероприятия
    load_events()
    print(f"✅ Загружено мероприятий: {len(EVENTS)}")
    
    # Настройка ЮKassa
    YOOKASSA_SHOP_ID = os.environ.get('YOOKASSA_SHOP_ID')
    YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY')
    
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY
        print(f"✅ ЮKassa настроен (Shop ID: {YOOKASSA_SHOP_ID})")
    else:
        print("❌ ЮKassa не настроен - проверьте переменные окружения")
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()

    # 1. Обработчик главного админ-меню (САМЫЙ ПЕРВЫЙ)
    app.add_handler(MessageHandler(
        filters.TEXT([
            "📊 Статистика", "🎭 Управление мероприятиями",
            "📦 Управление заказами", "🔍 Проверить билет",
            "🔙 Выход"
        ]) & filters.User(get_admin_ids()),
        admin_handler
    ))
    
    # 2. Обработчик меню управления мероприятиями
    app.add_handler(MessageHandler(
        filters.TEXT([
            "➕ Добавить мероприятие", "❌ Удалить мероприятие",
            "✏️ Редактировать билеты", "🖼️ Управление фото",
            "🔙 Назад"
        ]) & filters.User(get_admin_ids()),
        events_admin_handler
    ))
    
    # 3. Обработчик меню управления заказами
    app.add_handler(MessageHandler(
        filters.TEXT([
            "📝 Добавить существующий заказ", "📋 Просмотр всех заказов",
            "🔙 Назад"
        ]) & filters.User(get_admin_ids()),
        orders_admin_handler
    ))
    
    # 4. Обработчик для CSV файлов
    app.add_handler(MessageHandler(
        filters.Document.MimeType("text/csv") & filters.User(get_admin_ids()),
        handle_csv_upload
    ))
    
    # 5. Обработчик текстовых сообщений админов (для сложных операций)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(get_admin_ids()),
        process_admin_text
    ))
    
    # 6. ConversationHandler для добавления существующих заказов
    add_order_conv_handler = ConversationHandler(
        entry_points=[],
        states={
            ADDING_EXISTING_ORDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(get_admin_ids()), 
                             process_add_order_manually)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END,
        }
    )
    app.add_handler(add_order_conv_handler)
    
    # 7. ConversationHandler для покупки билетов
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            SELECTING_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_event)],
            SELECTING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_category)],
            SELECTING_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_quantity)],
            CONFIRMING: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_order)],
            PAYMENT_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_payment_step)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    
    # 8. Команды
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CommandHandler("check", check_ticket_command))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # 9. Обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Запускаем фоновую проверку pending платежей
    asyncio.get_event_loop().create_task(check_pending_payments())
    
    print("=== БОТ ЗАПУЩЕН ===")
    app.run_polling()

if __name__ == '__main__':
    main()            with open(EVENTS_FILE, 'r', encoding='utf-8') as file:
                events_data = json.load(file)
                EVENTS = normalize_ticket_structure(events_data)
                return EVENTS
        else:
            EVENTS = {}
            return {}
    except Exception as e:
        print(f"Ошибка загрузки мероприятий: {e}")
        EVENTS = {}
        return {}

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

# ===== ФУНКЦИИ ДЛЯ ДОБАВЛЕНИЯ СУЩЕСТВУЮЩИХ ЗАКАЗОВ =====

def add_existing_order(order_data):
    """Добавляет существующий заказ в базу данных"""
    try:
        # Проверяем обязательные поля
        required_fields = ['Дата', 'ID пользователя', 'Имя', 'Мероприятие', 
                          'Категория', 'Количество', 'Сумма', 'ID заказа']
        
        for field in required_fields:
            if field not in order_data or not order_data[field]:
                print(f"❌ Отсутствует обязательное поле: {field}")
                return False
        
        # Проверяем существует ли уже такой заказ
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for order in reader:
                    if order['ID заказа'] == order_data['ID заказа']:
                        print(f"⚠️ Заказ с ID {order_data['ID заказа']} уже существует")
                        return False
        
        # Добавляем заказ
        init_orders_file()  # Убедимся что файл существует
        
        with open(ORDERS_FILE, 'a', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file)
            writer.writerow([
                order_data.get('Дата', datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                order_data['ID пользователя'],
                order_data['Имя'],
                order_data['Мероприятие'],
                order_data['Категория'],
                order_data['Количество'],
                order_data['Сумма'],
                order_data['ID заказа'],
                order_data.get('Статус', 'active'),
                order_data.get('Payment ID', 'existing_order')
            ])
        
        print(f"✅ Существующий заказ добавлен: {order_data['ID заказа']}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка добавления существующего заказа: {e}")
        return False

def import_orders_from_csv(csv_filepath):
    """Импортирует заказы из CSV файла"""
    try:
        if not os.path.exists(csv_filepath):
            print(f"❌ Файл не найден: {csv_filepath}")
            return False
        
        added_count = 0
        skipped_count = 0
        
        with open(csv_filepath, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                # Преобразуем названия колонок если нужно
                order_data = {
                    'Дата': row.get('Дата', row.get('date', '')),
                    'ID пользователя': row.get('ID пользователя', row.get('user_id', '')),
                    'Имя': row.get('Имя', row.get('name', '')),
                    'Мероприятие': row.get('Мероприятие', row.get('event', '')),
                    'Категория': row.get('Категория', row.get('category', '')),
                    'Количество': row.get('Количество', row.get('quantity', '')),
                    'Сумма': row.get('Сумма', row.get('amount', '')),
                    'ID заказа': row.get('ID заказа', row.get('order_id', '')),
                    'Статус': row.get('Статус', row.get('status', 'active')),
                    'Payment ID': row.get('Payment ID', row.get('payment_id', 'existing_order'))
                }
                
                if add_existing_order(order_data):
                    added_count += 1
                else:
                    skipped_count += 1
        
        print(f"✅ Импорт завершен: добавлено {added_count}, пропущено {skipped_count}")
        return added_count > 0
        
    except Exception as e:
        print(f"❌ Ошибка импорта из CSV: {e}")
        return False

# ===== ОСНОВНЫЕ ФУНКЦИИ =====

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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы"""
    # Проверка билета по QR-коду
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
    
    # Вывод списка мероприятий с фото
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
        
        await asyncio.sleep(0.5)
    
    keyboard = [list(EVENTS.keys())]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎪 Добро пожаловать в систему покупки билетов!\n\n"
        "Выберите мероприятие из списка выше:",
        reply_markup=reply_markup
    )
    return SELECTING_EVENT

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
    
    # Пока используем базовую цену
    current_price = base_price
    
    context.user_data['category'] = category
    context.user_data['price'] = current_price
    context.user_data['base_price'] = base_price
    context.user_data['ticket_description'] = description
    
    response_text = f"🎟️ {category}\n💵 Цена: *{current_price} руб.*"
    
    if description:
        response_text += f"\n📝 {description}"
    
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
        return PAYMENT_STEP
        
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
        return PAYMENT_STEP

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
                "user_id": order_id.split('_')[0]
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
                "pending", payment.id
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

async def check_pending_payments():
    """Периодически проверяет статус pending платежей"""
    while True:
        try:
            await asyncio.sleep(30)
            print("🔍 Проверка pending платежей...")
            
            if not os.path.exists(ORDERS_FILE):
                continue
            
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                pending_orders = [order for order in reader if order['Статус'] == 'pending']
            
            for order in pending_orders:
                payment_id = order.get('Payment ID', '')
                if payment_id and payment_id != "no_payment_id":
                    try:
                        payment = Payment.find_one(payment_id)
                        
                        if payment.status == 'succeeded':
                            print(f"✅ Платеж подтвержден: {payment_id}")
                            update_order_status(order['ID заказа'], "active")
                            await send_ticket_after_payment(int(order['ID пользователя']), order['ID заказа'])
                            
                        elif payment.status in ['canceled', 'failed']:
                            print(f"❌ Платеж отменен: {payment_id}")
                            update_order_status(order['ID заказа'], "canceled")
                            
                    except Exception as e:
                        print(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                        
        except Exception as e:
            print(f"❌ Ошибка в check_pending_payments: {e}")

# ===== ФУНКЦИИ ДЛЯ ПРОВЕРКИ БИЛЕТОВ =====

async def check_ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки билетов"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    context.user_data['action'] = 'check_ticket'
    await update.message.reply_text(
        "📱 Введите код билета (ID заказа):",
        reply_markup=ReplyKeyboardRemove()
    )

async def check_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка билета"""
    if not is_admin(update.message.from_user.id):
        return
    
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
                parse_mode='Markdown'
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
                parse_mode='Markdown'
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

# ===== АДМИН-ПАНЕЛЬ =====

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для входа в админ-панель"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    keyboard = [
        ["📊 Статистика", "🎭 Управление мероприятиями"],
        ["📦 Управление заказами", "🔍 Проверить билет"],
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
    # Проверяем права админа
    if not is_admin(update.message.from_user.id):
        return
    
    choice = update.message.text
    
    if choice == "📊 Статистика":
        await show_stats(update, context)
        
    elif choice == "🎭 Управление мероприятиями":
        await manage_events_menu(update, context)
        
    elif choice == "📦 Управление заказами":
        await manage_orders_menu(update, context)
        
    elif choice == "🔍 Проверить билет":
        await check_ticket_command(update, context)
        
    elif choice == "🔙 Выход":
        await update.message.reply_text(
            "Выход из админ-панели",
            reply_markup=ReplyKeyboardRemove()
        )

async def manage_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления заказами"""
    keyboard = [
        ["📝 Добавить существующий заказ", "📋 Просмотр всех заказов"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        "📦 *Управление заказами*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def add_existing_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для добавления существующего заказа через админ-панель"""
    keyboard = [
        ["📝 Добавить вручную", "📁 Импорт из CSV"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📦 *Добавление существующих заказов*\n\n"
        "Выберите способ добавления:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def add_order_manually_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало ручного добавления заказа"""
    context.user_data.clear()
    context.user_data['action'] = 'add_existing_order'
    context.user_data['step'] = 'event'
    
    # Список мероприятий для выбора
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий. Сначала создайте мероприятие.")
        return await add_existing_order_command(update, context)
    
    keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 *Ручное добавление заказа*\n\n"
        "Выберите мероприятие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ADDING_EXISTING_ORDER

async def process_add_order_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ручного добавления заказа"""
    user_data = context.user_data
    text = update.message.text
    
    if text == "🔙 Назад":
        await add_existing_order_command(update, context)
        return ConversationHandler.END
    
    step = user_data.get('step')
    
    if step == 'event':
        if text not in EVENTS:
            await update.message.reply_text("❌ Пожалуйста, выберите мероприятие из списка:")
            return ADDING_EXISTING_ORDER
        
        user_data['event'] = text
        event_data = EVENTS[text]
        
        keyboard = [list(event_data['tickets'].keys()) + ["🔙 Назад"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Мероприятие: {text}\n"
            f"Выберите категорию билета:",
            reply_markup=reply_markup
        )
        user_data['step'] = 'category'
        return ADDING_EXISTING_ORDER
    
    elif step == 'category':
        if text == "🔙 Назад":
            user_data['step'] = 'event'
            keyboard = [list(EVENTS.keys()) + ["🔙 Назад"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "Выберите мероприятие:",
                reply_markup=reply_markup
            )
            return ADDING_EXISTING_ORDER
        
        if text not in EVENTS[user_data['event']]['tickets']:
            await update.message.reply_text("❌ Пожалуйста, выберите категорию из списка:")
            return ADDING_EXISTING_ORDER
        
        user_data['category'] = text
        user_data['step'] = 'user_id'
        
        await update.message.reply_text(
            f"Мероприятие: {user_data['event']}\n"
            f"Категория: {text}\n\n"
            f"Введите ID пользователя (Telegram ID):",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'user_id':
        try:
            user_id = int(text)
            user_data['user_id'] = user_id
            user_data['step'] = 'user_name'
            
            await update.message.reply_text(
                "Введите имя пользователя:",
                reply_markup=ReplyKeyboardRemove()
            )
            return ADDING_EXISTING_ORDER
        except ValueError:
            await update.message.reply_text("❌ ID должен быть числом. Введите ID пользователя:")
            return ADDING_EXISTING_ORDER
    
    elif step == 'user_name':
        user_data['user_name'] = text
        user_data['step'] = 'quantity'
        
        await update.message.reply_text(
            "Введите количество билетов:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'quantity':
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
            user_data['quantity'] = quantity
            user_data['step'] = 'amount'
            
            # Автоматически рассчитываем сумму
            event_name = user_data['event']
            category = user_data['category']
            ticket_price = EVENTS[event_name]['tickets'][category]['price']
            total_amount = ticket_price * quantity
            
            user_data['amount'] = total_amount
            user_data['step'] = 'order_id'
            
            await update.message.reply_text(
                f"Количество: {quantity}\n"
                f"Цена за билет: {ticket_price} руб.\n"
                f"Сумма автоматически рассчитана: {total_amount} руб.\n\n"
                f"Введите ID заказа (уникальный номер):",
                reply_markup=ReplyKeyboardRemove()
            )
            return ADDING_EXISTING_ORDER
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть положительным числом. Введите количество:")
            return ADDING_EXISTING_ORDER
    
    elif step == 'order_id':
        order_id = text.strip()
        
        # Проверяем уникальность ID заказа
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for order in reader:
                    if order['ID заказа'] == order_id:
                        await update.message.reply_text(
                            f"❌ Заказ с ID '{order_id}' уже существует. Введите другой ID:"
                        )
                        return ADDING_EXISTING_ORDER
        
        user_data['order_id'] = order_id
        user_data['step'] = 'confirm'
        
        # Формируем сводку
        summary = f"""
📋 *Сводка заказа:*

🎭 Мероприятие: {user_data['event']}
🎟️ Категория: {user_data['category']}
👤 ID пользователя: {user_data['user_id']}
👤 Имя: {user_data['user_name']}
🔢 Количество: {user_data['quantity']}
💵 Сумма: {user_data['amount']} руб.
🆔 ID заказа: {order_id}
📅 Дата: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

✅ Подтвердить добавление заказа?
        """
        
        keyboard = [["✅ Подтвердить", "❌ Отменить"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            summary,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return ADDING_EXISTING_ORDER
    
    elif step == 'confirm':
        if text == "✅ Подтвердить":
            # Создаем объект заказа
            order_data = {
                'Дата': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'ID пользователя': user_data['user_id'],
                'Имя': user_data['user_name'],
                'Мероприятие': user_data['event'],
                'Категория': user_data['category'],
                'Количество': user_data['quantity'],
                'Сумма': user_data['amount'],
                'ID заказа': user_data['order_id'],
                'Статус': 'active',
                'Payment ID': 'existing_order'
            }
            
            # Добавляем заказ
            if add_existing_order(order_data):
                # Генерируем QR-код
                qr_code = await generate_qr_code(user_data['order_id'])
                
                # Отправляем билет пользователю
                try:
                    ticket_message = f"""🎉 Ваш билет добавлен в систему!

📋 Электронный билет:
🎭 Мероприятие: {user_data['event']}
📅 Дата: {EVENTS[user_data['event']]['date']}
📍 Место: {EVENTS[user_data['event']]['location']}
🎟️ Категория: {user_data['category']}
🔢 Количество: {user_data['quantity']} шт.
💵 Сумма: {user_data['amount']} руб.
🆔 ID заказа: {user_data['order_id']}

📱 Сохраните этот QR-код! Он потребуется для входа на мероприятие."""
                    
                    if qr_code:
                        await context.bot.send_photo(
                            chat_id=user_data['user_id'],
                            photo=qr_code,
                            caption=ticket_message
                        )
                    
                    # Отправляем подтверждение админу
                    await update.message.reply_text(
                        f"✅ Заказ успешно добавлен!\n"
                        f"Билет отправлен пользователю {user_data['user_name']}",
                        reply_markup=ReplyKeyboardRemove()
                    )
                except Exception as e:
                    print(f"Ошибка отправки билета: {e}")
                    await update.message.reply_text(
                        f"✅ Заказ добавлен, но не удалось отправить билет пользователю: {e}",
                        reply_markup=ReplyKeyboardRemove()
                    )
            else:
                await update.message.reply_text(
                    "❌ Ошибка при добавлении заказа",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            await update.message.reply_text(
                "❌ Добавление заказа отменено",
                reply_markup=ReplyKeyboardRemove()
            )
        
        context.user_data.clear()
        return ConversationHandler.END

async def import_csv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало импорта из CSV"""
    await update.message.reply_text(
        "📁 *Импорт из CSV*\n\n"
        "Для импорта заказов:\n"
        "1. Подготовьте CSV файл со следующими колонками:\n"
        "   - Дата (формат: ГГГГ-ММ-ДД ЧЧ:ММ:СС)\n"
        "   - ID пользователя (Telegram ID)\n"
        "   - Имя\n"
        "   - Мероприятие\n"
        "   - Категория\n"
        "   - Количество\n"
        "   - Сумма\n"
        "   - ID заказа\n"
        "   - Статус (опционально, по умолчанию 'active')\n"
        "   - Payment ID (опционально, по умолчанию 'existing_order')\n\n"
        "2. Отправьте файл в этот чат",
        parse_mode='Markdown'
    )
    context.user_data['action'] = 'import_csv'

async def handle_csv_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загрузки CSV файла"""
    if not is_admin(update.message.from_user.id):
        return
    
    if context.user_data.get('action') != 'import_csv':
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ Пожалуйста, отправьте CSV файл")
        return
    
    file = await update.message.document.get_file()
    filename = update.message.document.file_name
    
    if not filename.lower().endswith('.csv'):
        await update.message.reply_text("❌ Пожалуйста, отправьте файл в формате CSV")
        return
    
    # Скачиваем файл
    temp_path = os.path.join(BASE_DIR, "temp_import.csv")
    await file.download_to_drive(temp_path)
    
    # Импортируем заказы
    await update.message.reply_text("🔄 Импорт заказов...")
    
    success = import_orders_from_csv(temp_path)
    
    # Удаляем временный файл
    if os.path.exists(temp_path):
        os.remove(temp_path)
    
    if success:
        await update.message.reply_text("✅ Импорт успешно завершен!")
    else:
        await update.message.reply_text("❌ Ошибка при импорте. Проверьте формат файла.")
    
    context.user_data.clear()
    await admin_command(update, context)

async def view_all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр всех заказов"""
    try:
        if not os.path.exists(ORDERS_FILE):
            await update.message.reply_text("📦 Нет заказов")
            return
        
        with open(ORDERS_FILE, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            orders = list(reader)
        
        if not orders:
            await update.message.reply_text("📦 Нет заказов")
            return
        
        message = "📋 *Все заказы:*\n\n"
        
        for i, order in enumerate(orders, 1):
            message += f"{i}. 🆔 {order['ID заказа']}\n"
            message += f"   👤 {order['Имя']} (ID: {order['ID пользователя']})\n"
            message += f"   🎭 {order['Мероприятие']} - {order['Категория']}\n"
            message += f"   🔢 {order['Количество']} шт. | 💵 {order['Сумма']} руб.\n"
            message += f"   📅 {order['Дата']} | Статус: {order['Статус']}\n\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке заказов: {e}")

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

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений в админ-панели"""
    if not is_admin(update.message.from_user.id):
        return
    
    text = update.message.text
    user_data = context.user_data
    
    print(f"DEBUG: Обработка текста '{text}', action: {user_data.get('action')}")
    
    if user_data.get('action') == 'check_ticket':
        await check_ticket(update, context)
        user_data.clear()
        return
    
    if text == "🔙 Назад":
        if user_data.get('action') == 'manage_orders':
            await admin_command(update, context)
        elif user_data.get('action') == 'import_csv':
            await add_existing_order_command(update, context)
        else:
            await admin_command(update, context)
        return
    
    if text == "📋 Просмотр всех заказов":
        await view_all_orders(update, context)
        return
    
    if text == "📝 Добавить вручную":
        return await add_order_manually_start(update, context)
    
    if text == "📁 Импорт из CSV":
        return await import_csv_start(update, context)
    
    if text == "➕ Добавить мероприятие":
        await add_event_start(update, context)
        
    elif text == "❌ Удалить мероприятие":
        await delete_event_start(update, context)
        
    elif text == "✏️ Редактировать билеты":
        await edit_tickets_start(update, context)
        
    elif text == "🖼️ Управление фото":
        await manage_photos_start(update, context)

# ===== БАЗОВЫЕ ФУНКЦИИ АДМИН-ПАНЕЛИ =====

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

async def process_add_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка добавления мероприятия"""
    user_data = context.user_data
    text = update.message.text
    
    if text == "🔙 Назад":
        await manage_events_menu(update, context)
        return
    
    step = user_data.get('step')
    
    if step == 'name':
        user_data['event_name'] = text
        user_data['step'] = 'date'
        
        await update.message.reply_text(
            "📅 Введите дату и время мероприятия (например: 2024-12-25 19:00):",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'date':
        user_data['event_date'] = text
        user_data['step'] = 'location'
        
        await update.message.reply_text(
            "📍 Введите место проведения мероприятия:",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif step == 'location':
        user_data['event_location'] = text
        user_data['step'] = 'description'
        
        keyboard = [["📝 Добавить описание", "⏭️ Пропустить описание"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📝 Хотите добавить описание мероприятия?",
            reply_markup=reply_markup
        )
        
    elif step == 'description':
        if text == "📝 Добавить описание":
            user_data['step'] = 'description_text'
            await update.message.reply_text(
                "Введите описание мероприятия:",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text == "⏭️ Пропустить описание":
            user_data['event_description'] = ""
            user_data['step'] = 'tickets'
            await show_tickets_menu(update, context)
        
    elif step == 'description_text':
        user_data['event_description'] = text
        user_data['step'] = 'tickets'
        await show_tickets_menu(update, context)

async def show_tickets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню для добавления билетов"""
    user_data = context.user_data
    
    summary = f"""
📋 *Сводка мероприятия:*

🎭 Название: {user_data.get('event_name', 'Не указано')}
📅 Дата: {user_data.get('event_date', 'Не указано')}
📍 Место: {user_data.get('event_location', 'Не указано')}
📝 Описание: {user_data.get('event_description', 'Нет описания')}

Теперь добавьте билеты к мероприятию:
    """
    
    keyboard = [
        ["🎫 Добавить билет"],
        ["✅ Завершить создание"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        summary,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def process_tickets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка меню билетов"""
    user_data = context.user_data
    text = update.message.text
    
    if text == "🎫 Добавить билет":
        user_data['ticket_step'] = 'name'
        user_data['adding_ticket'] = True
        
        await update.message.reply_text(
            "Введите название категории билета (например: Стандарт, VIP, Премиум):",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif text == "✅ Завершить создание":
        # Создаем мероприятие
        event_name = user_data['event_name']
        
        EVENTS[event_name] = {
            'date': user_data['event_date'],
            'location': user_data['event_location'],
            'description': user_data.get('event_description', ''),
            'photo': None,
            'tickets': user_data.get('event_tickets', {}),
            'pricing_rules': {}
        }
        
        if save_events(EVENTS):
            await update.message.reply_text(
                f"✅ Мероприятие '{event_name}' успешно создано!",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при сохранении мероприятия",
                reply_markup=ReplyKeyboardRemove()
            )
        
        user_data.clear()
        await admin_command(update, context)
        
    elif text == "🔙 Назад":
        user_data['step'] = 'description'
        keyboard = [["📝 Добавить описание", "⏭️ Пропустить описание"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📝 Хотите добавить описание мероприятия?",
            reply_markup=reply_markup
        )

async def process_add_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка добавления билета"""
    user_data = context.user_data
    text = update.message.text
    ticket_step = user_data.get('ticket_step')
    
    if ticket_step == 'name':
        user_data['ticket_name'] = text
        user_data['ticket_step'] = 'price'
        
        await update.message.reply_text(
            "💵 Введите цену билета (только число, в рублях):",
            reply_markup=ReplyKeyboardRemove()
        )
        
    elif ticket_step == 'price':
        try:
            price = int(text)
            if price <= 0:
                raise ValueError
            
            user_data['ticket_price'] = price
            user_data['ticket_step'] = 'description'
            
            keyboard = [["📝 Добавить описание", "⏭️ Пропустить"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                "📝 Хотите добавить описание для этой категории билета?",
                reply_markup=reply_markup
            )
            
        except ValueError:
            await update.message.reply_text(
                "❌ Цена должна быть положительным числом. Введите цену:",
                reply_markup=ReplyKeyboardRemove()
            )
            
    elif ticket_step == 'description':
        if text == "📝 Добавить описание":
            user_data['ticket_step'] = 'description_text'
            await update.message.reply_text(
                "Введите описание категории билета:",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text == "⏭️ Пропустить":
            # Сохраняем билет без описания
            ticket_name = user_data['ticket_name']
            ticket_price = user_data['ticket_price']
            
            # Инициализируем словарь билетов если его нет
            if 'event_tickets' not in user_data:
                user_data['event_tickets'] = {}
            
            user_data['event_tickets'][ticket_name] = {
                'price': ticket_price,
                'description': ''
            }
            
            # Сбрасываем данные билета
            user_data.pop('ticket_name', None)
            user_data.pop('ticket_price', None)
            user_data.pop('ticket_step', None)
            user_data.pop('adding_ticket', None)
            
            # Показываем меню билетов с обновленной информацией
            await show_tickets_menu_with_added(update, context)
            
    elif ticket_step == 'description_text':
        ticket_name = user_data['ticket_name']
        ticket_price = user_data['ticket_price']
        ticket_description = text
        
        # Инициализируем словарь билетов если его нет
        if 'event_tickets' not in user_data:
            user_data['event_tickets'] = {}
        
        user_data['event_tickets'][ticket_name] = {
            'price': ticket_price,
            'description': ticket_description
        }
        
        # Сбрасываем данные билета
        user_data.pop('ticket_name', None)
        user_data.pop('ticket_price', None)
        user_data.pop('ticket_step', None)
        user_data.pop('adding_ticket', None)
        
        # Показываем меню билетов с обновленной информацией
        await show_tickets_menu_with_added(update, context)

async def show_tickets_menu_with_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню билетов с уже добавленными билетами"""
    user_data = context.user_data
    
    summary = f"""
📋 *Сводка мероприятия:*

🎭 Название: {user_data.get('event_name', 'Не указано')}
📅 Дата: {user_data.get('event_date', 'Не указано')}
📍 Место: {user_data.get('event_location', 'Не указано')}
📝 Описание: {user_data.get('event_description', 'Нет описания')}

🎟️ *Добавленные билеты:*
"""
    
    if 'event_tickets' in user_data and user_data['event_tickets']:
        for ticket_name, ticket_info in user_data['event_tickets'].items():
            price = ticket_info['price']
            description = ticket_info['description']
            summary += f"• {ticket_name}: {price} руб."
            if description:
                summary += f" ({description})"
            summary += "\n"
    else:
        summary += "• Пока нет добавленных билетов\n"
    
    summary += "\nВыберите действие:"
    
    keyboard = [
        ["🎫 Добавить билет"],
        ["✅ Завершить создание"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        summary,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений в админ-панели"""
    if not is_admin(update.message.from_user.id):
        return
    
    text = update.message.text
    user_data = context.user_data
    
    print(f"DEBUG: Обработка текста '{text}', action: {user_data.get('action')}")
    
    # Обработка проверки билетов
    if user_data.get('action') == 'check_ticket':
        await check_ticket(update, context)
        user_data.clear()
        return
    
    # Обработка добавления мероприятия
    if user_data.get('action') == 'add_event':
        await process_add_event(update, context)
        return
    
    # Обработка добавления билетов к мероприятию
    if user_data.get('action') == 'add_event' and user_data.get('adding_ticket'):
        await process_add_ticket(update, context)
        return
    
    # Обработка меню билетов
    if user_data.get('action') == 'add_event' and user_data.get('step') == 'tickets':
        await process_tickets_menu(update, context)
        return
    
    # Навигация
    if text == "🔙 Назад":
        if user_data.get('action') == 'manage_orders':
            await admin_command(update, context)
        elif user_data.get('action') == 'import_csv':
            await add_existing_order_command(update, context)
        elif user_data.get('action') == 'add_event':
            await manage_events_menu(update, context)
        else:
            await admin_command(update, context)
        user_data.clear()
        return
    
    # Обработка кнопок из меню управления мероприятиями
    if text == "➕ Добавить мероприятие":
        await add_event_start(update, context)
        return
        
    elif text == "❌ Удалить мероприятие":
        await delete_event_start(update, context)
        return
        
    elif text == "✏️ Редактировать билеты":
        await edit_tickets_start(update, context)
        return
        
    elif text == "🖼️ Управление фото":
        await manage_photos_start(update, context)
        return
    
    # Обработка кнопок из меню управления заказами
    if text == "📋 Просмотр всех заказов":
        await view_all_orders(update, context)
        return
    
    if text == "📝 Добавить вручную":
        return await add_order_manually_start(update, context)
    
    if text == "📁 Импорт из CSV":
        return await import_csv_start(update, context)

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

async def edit_tickets_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования билетов"""
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий для редактирования")
        return
    
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

async def manage_photos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало управления фото мероприятий"""
    if not EVENTS:
        await update.message.reply_text("❌ Нет мероприятий для управления фото")
        return
    
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

# ===== ОСТАЛЬНЫЕ КОМАНДЫ =====

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
        
        await asyncio.sleep(0.5)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Операция отменена",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    print(f"Ошибка: {context.error}")
    try:
        if update and update.message:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте еще раз.")
    except:
        pass

# ===== ГЛАВНАЯ ФУНКЦИЯ =====

def main():
    print("=== ЗАПУСК БОТА ===")
    
    # Инициализация
    global BOT_TOKEN
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не найден! Установите переменную окружения BOT_TOKEN")
    
    init_directories()
    update_orders_file()
    
    # Загружаем мероприятия
    load_events()
    print(f"✅ Загружено мероприятий: {len(EVENTS)}")
    
    # Настройка ЮKassa
    YOOKASSA_SHOP_ID = os.environ.get('YOOKASSA_SHOP_ID')
    YOOKASSA_SECRET_KEY = os.environ.get('YOOKASSA_SECRET_KEY')
    
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY
        print(f"✅ ЮKassa настроен (Shop ID: {YOOKASSA_SHOP_ID})")
    else:
        print("❌ ЮKassa не настроен - проверьте переменные окружения")
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()

    # 1. ConversationHandler для добавления существующих заказов
    add_order_conv_handler = ConversationHandler(
        entry_points=[],  # Вход через админ-панель
        states={
            ADDING_EXISTING_ORDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(get_admin_ids()), 
                             process_add_order_manually)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END,
        }
    )
    app.add_handler(add_order_conv_handler)
    
    # 2. Обработчик админ-панели (ПЕРВЫЙ)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(get_admin_ids()),
        admin_handler
    ))
    
    # 3. Обработчик для CSV файлов
    app.add_handler(MessageHandler(
        filters.Document.MimeType("text/csv") & filters.User(get_admin_ids()),
        handle_csv_upload
    ))
    
    # 4. Обработчик текстовых сообщений админов
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(get_admin_ids()),
        process_admin_text
    ))
    
    # 5. ConversationHandler для покупки билетов
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            SELECTING_EVENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_event)],
            SELECTING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_category)],
            SELECTING_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_quantity)],
            CONFIRMING: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_order)],
            PAYMENT_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_payment_step)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    
    # 6. Команды
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CommandHandler("check", check_ticket_command))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # 7. Обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Запускаем фоновую проверку pending платежей
    asyncio.get_event_loop().create_task(check_pending_payments())
    
    print("=== БОТ ЗАПУЩЕН ===")
    app.run_polling()

if __name__ == '__main__':
    main()


