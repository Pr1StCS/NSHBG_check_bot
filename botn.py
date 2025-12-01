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
ADDING_EXISTING_ORDER = 6  # Новое состояние для добавления существующих заказов

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

async def add_existing_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для добавления существующего заказа через админ-панель"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
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

# ===== ОСТАЛЬНОЙ КОД (с дополнениями) =====

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
    print(f"DEBUG: Админ {update.message.from_user.id} выбрал: '{choice}'")
    
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
    
    # Обработка кнопок подменю
    elif choice in ["➕ Добавить мероприятие", "❌ Удалить мероприятие", 
                   "✏️ Редактировать билеты", "🖼️ Управление фото"]:
        await process_admin_buttons(update, context)
    elif choice in ["📝 Добавить существующий заказ", "📋 Просмотр всех заказов",
                   "🔍 Поиск заказа", "📤 Экспорт заказов"]:
        await process_orders_buttons(update, context)

async def manage_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления заказами"""
    keyboard = [
        ["📝 Добавить существующий заказ", "📋 Просмотр всех заказов"],
        ["🔍 Поиск заказа", "📤 Экспорт заказов"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        "📦 *Управление заказами*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def process_orders_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок меню заказов"""
    choice = update.message.text
    
    if choice == "📝 Добавить существующий заказ":
        await add_existing_order_command(update, context)
        
    elif choice == "📋 Просмотр всех заказов":
        await view_all_orders(update, context)
        
    elif choice == "🔍 Поиск заказа":
        await search_order_start(update, context)
        
    elif choice == "📤 Экспорт заказов":
        await export_orders(update, context)

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
        
        # Разбиваем на страницы по 10 заказов
        orders_per_page = 10
        total_pages = (len(orders) + orders_per_page - 1) // orders_per_page
        
        context.user_data['orders_list'] = orders
        context.user_data['current_page'] = 0
        context.user_data['total_pages'] = total_pages
        context.user_data['action'] = 'view_orders'
        
        await show_orders_page(update, context)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке заказов: {e}")

async def show_orders_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает страницу с заказами"""
    user_data = context.user_data
    orders = user_data.get('orders_list', [])
    current_page = user_data.get('current_page', 0)
    total_pages = user_data.get('total_pages', 1)
    
    start_idx = current_page * 10
    end_idx = min(start_idx + 10, len(orders))
    
    page_orders = orders[start_idx:end_idx]
    
    message = f"📋 *Все заказы*\n"
    message += f"Страница {current_page + 1} из {total_pages}\n"
    message += f"Показано {len(page_orders)} из {len(orders)} заказов\n\n"
    
    for i, order in enumerate(page_orders, start=start_idx + 1):
        message += f"{i}. 🆔 {order['ID заказа']}\n"
        message += f"   👤 {order['Имя']} (ID: {order['ID пользователя']})\n"
        message += f"   🎭 {order['Мероприятие']} - {order['Категория']}\n"
        message += f"   🔢 {order['Количество']} шт. | 💵 {order['Сумма']} руб.\n"
        message += f"   📅 {order['Дата']} | Статус: {order['Статус']}\n\n"
    
    keyboard = []
    if current_page > 0:
        keyboard.append("⬅️ Назад")
    if current_page < total_pages - 1:
        keyboard.append("➡️ Вперед")
    
    if keyboard:
        reply_markup = ReplyKeyboardMarkup([keyboard + ["🔙 Назад"]], resize_keyboard=True)
    else:
        reply_markup = ReplyKeyboardMarkup([["🔙 Назад"]], resize_keyboard=True)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def search_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало поиска заказа"""
    context.user_data['action'] = 'search_order'
    context.user_data['search_step'] = 'type'
    
    keyboard = [
        ["🔍 По ID заказа", "👤 По ID пользователя"],
        ["📞 По номеру телефона", "🎭 По мероприятию"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔍 *Поиск заказа*\n\n"
        "Выберите тип поиска:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт заказов в CSV"""
    try:
        if not os.path.exists(ORDERS_FILE):
            await update.message.reply_text("📦 Нет заказов для экспорта")
            return
        
        # Создаем временный файл для отправки
        temp_file = os.path.join(BASE_DIR, "orders_export.csv")
        
        # Копируем файл заказов
        import shutil
        shutil.copy2(ORDERS_FILE, temp_file)
        
        # Отправляем файл
        with open(temp_file, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename="orders_export.csv",
                caption="📤 Экспорт заказов завершен"
            )
        
        # Удаляем временный файл
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при экспорте: {e}")

# ===== ОСТАЛЬНОЙ КОД АДМИН-ПАНЕЛИ (из предыдущего примера) =====

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений в админ-панели"""
    if not is_admin(update.message.from_user.id):
        return
    
    text = update.message.text
    user_data = context.user_data
    
    print(f"DEBUG: Обработка текста '{text}', action: {user_data.get('action')}")
    
    # Обработка кнопок навигации по страницам заказов
    if user_data.get('action') == 'view_orders':
        if text == "⬅️ Назад":
            user_data['current_page'] -= 1
            await show_orders_page(update, context)
            return
        elif text == "➡️ Вперед":
            user_data['current_page'] += 1
            await show_orders_page(update, context)
            return
        elif text == "🔙 Назад":
            user_data.clear()
            await manage_orders_menu(update, context)
            return
    
    # Обработка добавления существующего заказа
    if user_data.get('action') == 'add_existing_order':
        if text == "📝 Добавить вручную":
            return await add_order_manually_start(update, context)
        elif text == "📁 Импорт из CSV":
            return await import_csv_start(update, context)
        elif text == "🔙 Назад":
            user_data.clear()
            await manage_orders_menu(update, context)
            return
    
    # Обработка ручного добавления заказа
    if user_data.get('action') == 'add_existing_order' and user_data.get('step'):
        return await process_add_order_manually(update, context)
    
    # Стандартная обработка (как в предыдущем коде)
    if text == "🔙 Назад":
        if user_data.get('action') == 'manage_events':
            await admin_command(update, context)
        elif user_data.get('action') == 'manage_orders':
            await manage_orders_menu(update, context)
        else:
            await admin_command(update, context)
        return
    
    # ... остальная обработка как в предыдущем коде ...

# ===== ОСТАЛЬНЫЕ ФУНКЦИИ =====

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

# ===== ДОБАВЛЕНИЕ ОБРАБОТЧИКОВ ДЛЯ CSV ФАЙЛОВ =====

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
