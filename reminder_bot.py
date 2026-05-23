import sqlite3
import re
import os
import threading
import time
from datetime import datetime, timedelta
import pytz
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes
)
import warnings
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

# ========== НАСТРОЙКИ ==========
TOKEN = "8804067266:AAGtThyM_bZQxuaSbyZh5Es5NJXzPU-PXL4"
DB_FILE = "reminders.db"
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

ASK_NAME = 10
TEXT, DATE, TIME, REPEAT_TYPE, REPEAT_CUSTOM, FOR_WHOM = range(20, 26)

REPEAT_OPTIONS = {
    "once": "Один раз",
    "hourly": "Каждый час",
    "daily": "Каждый день",
    "custom_days": "Раз в N дней",
    "weekly": "Каждую неделю",
    "monthly": "Каждый месяц"
}

user_temp = {}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  for_user_id INTEGER,
                  text TEXT,
                  remind_time TEXT,
                  repeat_type TEXT,
                  repeat_value INTEGER,
                  created_at TEXT)''')
    c.execute("PRAGMA table_info(reminders)")
    columns = [col[1] for col in c.fetchall()]
    if "repeat_value" not in columns:
        c.execute("ALTER TABLE reminders ADD COLUMN repeat_value INTEGER")
    conn.commit()
    conn.close()

def get_user_name(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_user_name(user_id, name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, name) VALUES (?, ?)", (user_id, name))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, name FROM users")
    rows = c.fetchall()
    conn.close()
    return rows

def add_reminder(for_user_id, text, remind_time, repeat_type, repeat_value=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO reminders (for_user_id, text, remind_time, repeat_type, repeat_value, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (for_user_id, text, remind_time, repeat_type, repeat_value, datetime.now(MOSCOW_TZ).isoformat()))
    conn.commit()
    conn.close()

def get_reminders_for_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, for_user_id, text, remind_time, repeat_type, repeat_value FROM reminders WHERE for_user_id = ? ORDER BY remind_time", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_reminder(reminder_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def get_due_reminders():
    now_msk = datetime.now(MOSCOW_TZ)
    now_str = now_msk.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, for_user_id, text, remind_time, repeat_type, repeat_value FROM reminders WHERE remind_time <= ?", (now_str,))
    rows = c.fetchall()
    conn.close()
    return rows

def update_next_reminder(reminder_id, repeat_type, repeat_value, current_time_str):
    dt = datetime.fromisoformat(current_time_str)
    if repeat_type == "once":
        return None
    elif repeat_type == "hourly":
        next_dt = dt + timedelta(hours=1)
    elif repeat_type == "daily":
        next_dt = dt + timedelta(days=1)
    elif repeat_type == "custom_days" and repeat_value:
        next_dt = dt + timedelta(days=repeat_value)
    elif repeat_type == "weekly":
        next_dt = dt + timedelta(weeks=1)
    elif repeat_type == "monthly":
        next_dt = dt + timedelta(days=32)
        next_dt = next_dt.replace(day=min(dt.day, (next_dt.replace(day=1) + timedelta(days=32)).day))
    else:
        return None
    next_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE reminders SET remind_time = ? WHERE id = ?", (next_str, reminder_id))
    conn.commit()
    conn.close()
    return next_str

def delete_reminder_by_id(reminder_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

# ========== КАЛЕНДАРЬ ==========
def get_calendar_keyboard(year, month):
    first_day = datetime(year, month, 1)
    start_weekday = first_day.weekday()
    days_in_month = (first_day.replace(month=month+1, day=1) - timedelta(days=1)).day if month < 12 else 31
    keyboard = []
    keyboard.append([InlineKeyboardButton(f"{year}-{month:02d}", callback_data="ignore")])
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(d, callback_data="ignore") for d in week_days])
    row = []
    for _ in range(start_weekday):
        row.append(InlineKeyboardButton(" ", callback_data="ignore"))
    for day in range(1, days_in_month+1):
        row.append(InlineKeyboardButton(str(day), callback_data=f"date_{year}_{month}_{day}"))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    nav = []
    if month > 1:
        nav.append(InlineKeyboardButton("◀️ Пред", callback_data=f"cal_prev_{year}_{month-1}"))
    else:
        nav.append(InlineKeyboardButton(" ", callback_data="ignore"))
    if month < 12:
        nav.append(InlineKeyboardButton("След ▶️", callback_data=f"cal_next_{year}_{month+1}"))
    else:
        nav.append(InlineKeyboardButton(" ", callback_data="ignore"))
    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
    return InlineKeyboardMarkup(keyboard)

# ========== МЕНЮ ==========
async def send_main_menu(chat_id, context, text="📌 Главное меню:"):
    keyboard = [
        [InlineKeyboardButton("➕ Добавить напоминание", callback_data="add")],
        [InlineKeyboardButton("📋 Мои напоминания", callback_data="list")],
    ]
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

# ========== СТАРТ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = get_user_name(user_id)
    if name is None:
        await update.message.reply_text("Привет! Как тебя зовут? (Напиши своё имя)")
        return ASK_NAME
    else:
        await update.message.reply_text(f"С возвращением, {name}!")
        await send_main_menu(update.effective_chat.id, context)
        return ConversationHandler.END

async def ask_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.message.text.strip()
    set_user_name(user_id, name)
    await update.message.reply_text(f"Отлично, {name}! Теперь я буду к тебе так обращаться.")
    await send_main_menu(update.effective_chat.id, context)
    return ConversationHandler.END

# ---------- ДОБАВЛЕНИЕ ----------
async def add_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_temp[query.from_user.id] = {}
    await query.edit_message_text("Введите текст напоминания:")
    return TEXT

async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_temp[user_id]["text"] = update.message.text
    now = datetime.now(MOSCOW_TZ)
    await update.message.reply_text("Выберите дату:", reply_markup=get_calendar_keyboard(now.year, now.month))
    return DATE

async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("cal_prev_"):
        _, _, year, month = data.split("_")
        year, month = int(year), int(month)
        await query.edit_message_reply_markup(reply_markup=get_calendar_keyboard(year, month))
        return DATE
    elif data.startswith("cal_next_"):
        _, _, year, month = data.split("_")
        year, month = int(year), int(month)
        await query.edit_message_reply_markup(reply_markup=get_calendar_keyboard(year, month))
        return DATE
    elif data.startswith("date_"):
        _, year, month, day = data.split("_")
        user_temp[user_id]["date"] = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        await query.edit_message_text(f"📅 Дата: {user_temp[user_id]['date']}\n\nТеперь введите время в формате ЧЧ:ММ (например, 14:30)\nИли выберите час из кнопок:")
        kb = []
        for h in range(0, 24, 3):
            kb.append([InlineKeyboardButton(f"{h:02d}:00", callback_data=f"hour_{h}")])
        kb.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
        await query.message.reply_text("⏰ Часы:", reply_markup=InlineKeyboardMarkup(kb))
        return TIME
    elif data == "cancel_add":
        await query.edit_message_text("Добавление отменено.")
        await send_main_menu(query.message.chat_id, context)
        return ConversationHandler.END
    else:
        return DATE

async def manual_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    time_str = update.message.text.strip()
    if not re.match(r'^\d{1,2}:\d{2}$', time_str):
        await update.message.reply_text("❌ Неверный формат. Напишите время в формате ЧЧ:ММ, например 14:30")
        return TIME
    hour, minute = map(int, time_str.split(':'))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("❌ Час от 0 до 23, минуты от 0 до 59")
        return TIME
    date_str = user_temp[user_id]["date"]
    full_dt_str = f"{date_str} {hour:02d}:{minute:02d}:00"
    try:
        dt = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S")
        dt_msk = MOSCOW_TZ.localize(dt)
        if dt_msk < datetime.now(MOSCOW_TZ):
            await update.message.reply_text("⏰ Нельзя установить напоминание в прошлое! Выберите другую дату/время.")
            now = datetime.now(MOSCOW_TZ)
            await update.message.reply_text("Выберите дату:", reply_markup=get_calendar_keyboard(now.year, now.month))
            return DATE
        user_temp[user_id]["datetime"] = dt_msk.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        await update.message.reply_text("Ошибка, попробуйте ещё раз.")
        return DATE
    keyboard = [[InlineKeyboardButton(label, callback_data=f"rep_{key}")] for key, label in REPEAT_OPTIONS.items()]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
    await update.message.reply_text("🔄 Как часто напоминать?", reply_markup=InlineKeyboardMarkup(keyboard))
    return REPEAT_TYPE

async def time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("hour_"):
        hour = data.split("_")[1]
        user_temp[user_id]["hour"] = hour
        kb = [
            [InlineKeyboardButton("00", callback_data=f"min_{hour}_00"),
             InlineKeyboardButton("15", callback_data=f"min_{hour}_15"),
             InlineKeyboardButton("30", callback_data=f"min_{hour}_30"),
             InlineKeyboardButton("45", callback_data=f"min_{hour}_45")],
            [InlineKeyboardButton("✍️ Ввести минуты вручную", callback_data=f"manual_min_{hour}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")]
        ]
        await query.edit_message_text(f"⏰ Выберите минуты или введите их вручную (0–59):", reply_markup=InlineKeyboardMarkup(kb))
        return TIME
    elif data.startswith("min_"):
        _, hour, minute = data.split("_")
        full_dt_str = f"{user_temp[user_id]['date']} {int(hour):02d}:{int(minute):02d}:00"
        dt = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S")
        dt_msk = MOSCOW_TZ.localize(dt)
        if dt_msk < datetime.now(MOSCOW_TZ):
            await query.edit_message_text("⏰ Нельзя в прошлое! Выберите другую дату/время.")
            now = datetime.now(MOSCOW_TZ)
            await query.message.reply_text("Выберите дату:", reply_markup=get_calendar_keyboard(now.year, now.month))
            return DATE
        user_temp[user_id]["datetime"] = dt_msk.strftime("%Y-%m-%d %H:%M:%S")
        keyboard = [[InlineKeyboardButton(label, callback_data=f"rep_{key}")] for key, label in REPEAT_OPTIONS.items()]
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
        await query.edit_message_text("🔄 Как часто напоминать?", reply_markup=InlineKeyboardMarkup(keyboard))
        return REPEAT_TYPE
    elif data.startswith("manual_min_"):
        hour = data.split("_")[2]
        user_temp[user_id]["hour"] = hour
        await query.edit_message_text("✏️ Напишите число от 0 до 59 (минуты):")
        return TIME
    elif data == "cancel_add":
        await query.edit_message_text("Добавление отменено.")
        await send_main_menu(query.message.chat_id, context)
        return ConversationHandler.END
    else:
        return TIME

async def manual_minutes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    minute_str = update.message.text.strip()
    if not minute_str.isdigit():
        await update.message.reply_text("❌ Введите целое число (минуты от 0 до 59)")
        return TIME
    minute = int(minute_str)
    if minute < 0 or minute > 59:
        await update.message.reply_text("❌ Минуты должны быть от 0 до 59")
        return TIME
    hour = user_temp[user_id].get("hour")
    if hour is None:
        await update.message.reply_text("Ошибка, начните заново.")
        return ConversationHandler.END
    full_dt_str = f"{user_temp[user_id]['date']} {int(hour):02d}:{minute:02d}:00"
    dt = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S")
    dt_msk = MOSCOW_TZ.localize(dt)
    if dt_msk < datetime.now(MOSCOW_TZ):
        await update.message.reply_text("⏰ Нельзя в прошлое! Выберите другую дату/время.")
        now = datetime.now(MOSCOW_TZ)
        await update.message.reply_text("Выберите дату:", reply_markup=get_calendar_keyboard(now.year, now.month))
        return DATE
    user_temp[user_id]["datetime"] = dt_msk.strftime("%Y-%m-%d %H:%M:%S")
    keyboard = [[InlineKeyboardButton(label, callback_data=f"rep_{key}")] for key, label in REPEAT_OPTIONS.items()]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
    await update.message.reply_text("🔄 Как часто напоминать?", reply_markup=InlineKeyboardMarkup(keyboard))
    return REPEAT_TYPE

async def repeat_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("rep_"):
        rep_type = data.split("_")[1]
        if rep_type == "custom_days":
            user_temp[user_id]["repeat_type"] = rep_type
            await query.edit_message_text("📆 Введите количество дней (целое число, например 3):")
            return REPEAT_CUSTOM
        else:
            user_temp[user_id]["repeat_type"] = rep_type
            user_temp[user_id]["repeat_value"] = None
            users = get_all_users()
            keyboard = []
            current_name = get_user_name(user_id)
            for uid, name in users:
                label = f"Мне ({current_name})" if uid == user_id else name
                keyboard.append([InlineKeyboardButton(label, callback_data=f"for_{uid}")])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
            await query.edit_message_text("👥 Кому отправить напоминание?", reply_markup=InlineKeyboardMarkup(keyboard))
            return FOR_WHOM
    elif data == "cancel_add":
        await query.edit_message_text("Добавление отменено.")
        await send_main_menu(query.message.chat_id, context)
        return ConversationHandler.END
    else:
        return REPEAT_TYPE

async def custom_days_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        days = int(update.message.text.strip())
        if days <= 0:
            raise ValueError
        user_temp[user_id]["repeat_value"] = days
        users = get_all_users()
        keyboard = []
        current_name = get_user_name(user_id)
        for uid, name in users:
            label = f"Мне ({current_name})" if uid == user_id else name
            keyboard.append([InlineKeyboardButton(label, callback_data=f"for_{uid}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")])
        await update.message.reply_text("👥 Кому отправить напоминание?", reply_markup=InlineKeyboardMarkup(keyboard))
        return FOR_WHOM
    except:
        await update.message.reply_text("❌ Введите положительное целое число (например, 3)")
        return REPEAT_CUSTOM

async def for_whom_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    if data.startswith("for_"):
        for_user_id = int(data.split("_")[1])
        add_reminder(
            for_user_id=for_user_id,
            text=user_temp[user_id]["text"],
            remind_time=user_temp[user_id]["datetime"],
            repeat_type=user_temp[user_id]["repeat_type"],
            repeat_value=user_temp[user_id].get("repeat_value")
        )
        await query.edit_message_text("✅ Напоминание добавлено!")
        await send_main_menu(query.message.chat_id, context)
        return ConversationHandler.END
    elif data == "cancel_add":
        await query.edit_message_text("Добавление отменено.")
        await send_main_menu(query.message.chat_id, context)
        return ConversationHandler.END
    else:
        return FOR_WHOM

# ========== СПИСОК НАПОМИНАНИЙ ==========
async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    reminders = get_reminders_for_user(user_id)
    if not reminders:
        await query.edit_message_text("📭 У вас нет активных напоминаний.")
        await send_main_menu(query.message.chat_id, context)
        return
    text = "📋 Ваши напоминания:\n\n"
    keyboard = []
    for rem in reminders:
        rem_id, for_uid, rem_text, rem_time, rep_type, rep_val = rem
        recipient = "вам" if for_uid == user_id else get_user_name(for_uid) or "друг"
        repeat_str = REPEAT_OPTIONS.get(rep_type, rep_type)
        if rep_type == "custom_days" and rep_val:
            repeat_str += f" ({rep_val} дн.)"
        text += f"🔹 ID: {rem_id} | Для {recipient}\n   {rem_text}\n   на {rem_time} [{repeat_str}]\n\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Удалить #{rem_id}", callback_data=f"del_{rem_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rem_id = int(query.data.split("_")[1])
    delete_reminder(rem_id)
    await query.answer("Напоминание удалено!", show_alert=False)
    await list_reminders(update, context)

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await send_main_menu(query.message.chat_id, context)

# ========== ОТПРАВКА НАПОМИНАНИЙ ==========
async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    due = get_due_reminders()
    for rem_id, for_user_id, text, remind_time, repeat_type, repeat_val in due:
        name = get_user_name(for_user_id) or "Друг"
        message = f"🔔 {name}, напоминание!\n{text}"
        try:
            await context.bot.send_message(chat_id=for_user_id, text=message)
        except Exception as e:
            print(f"Не удалось отправить {for_user_id}: {e}")
        if repeat_type == "once":
            delete_reminder_by_id(rem_id)
        else:
            next_time = update_next_reminder(rem_id, repeat_type, repeat_val, remind_time)
            if next_time is None:
                delete_reminder_by_id(rem_id)

# ========== ЗАПУСК ==========
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    name_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_handler)]},
        fallbacks=[],
    )

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_button, pattern="^add$")],
        states={
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)],
            DATE: [CallbackQueryHandler(calendar_callback, pattern="^(cal_|date_|cancel_add)")],
            TIME: [
                CallbackQueryHandler(time_callback, pattern="^(hour_|min_|manual_min_|cancel_add)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_time_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_minutes_handler)
            ],
            REPEAT_TYPE: [CallbackQueryHandler(repeat_choice, pattern="^(rep_|cancel_add)")],
            REPEAT_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_days_handler)],
            FOR_WHOM: [CallbackQueryHandler(for_whom_choice, pattern="^(for_|cancel_add)")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: send_main_menu(u.effective_chat.id, c))],
        allow_reentry=True,
    )

    app.add_handler(name_conv)
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(list_reminders, pattern="^list$"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern="^main_menu$"))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(send_reminders, interval=60, first=10)
        print("JobQueue запущен")
    else:
        print("Установите 'pip install python-telegram-bot[job-queue]'")

    # Запускаем бота
    app.run_polling()

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Бот работает!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    # Запускаем бота в основном потоке
    main()
