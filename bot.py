import sqlite3
import time
import asyncio
from aiogram import Bot, Router, Dispatcher
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import Command
from aiogram import F
from collections import defaultdict
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

# Токен бота
TOKEN = "7790837350:AAEZ8txZk-8osGKA3eI9CxWHY_sG5uKBONo"  # noqa

# ID канала
ALLOWED_CHAT_ID = -1002634017713

# Переменные челленджа
TOTAL_CLICKS_NEEDED = 10000
CHALLENGE_DURATION = 24 * 60 * 60  # 24 часа
UPDATE_INTERVAL = 5  # Обновление сообщения раз в 5 секунд, если есть клики
CLICK_COOLDOWN = 0.5  # Ограничение: 1 клик каждые 0.5 секунды

# Сюжетные события с шагом 1000
PLOT_THRESHOLDS = {
    1000: " 1000! Табуреты начинают нервничать, а корабль подёргивается! 🪑",  # noqa
    3000: "🚨 3000! На корабле Кифирунцев начинает что-то дымить",  # noqa
    5000: "💥 5000! Половина пути пройдена! На корабле отключилось электричество! 🪑",  # noqa
    7000: "🌪 7000! На корабле начинается паника, табуреты пытаются чинить системы. 🪑",  # noqa
    9000: "🎯 9000! Последний рывок! Табуреты не справляются с поломками и готовят эвакуацию! 🪑",
    9500: "🎯 9500! Начинается эвакуация! Корабль еле держиться в воздухе🪑",# noqa
    9900: "🎯 9900! Корабль летит к земле, а табуреты из последних сил пытаются эвакуироваться!🪑",
}

# Инициализация бота и роутера
bot = Bot(token=TOKEN)
router = Router()

# Инициализация базы данных SQLite
conn = sqlite3.connect("challenge.db", check_same_thread=False)
cursor = conn.cursor()

# Создаём таблицы
cursor.execute('''
    CREATE TABLE IF NOT EXISTS challenge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clicks INTEGER DEFAULT 0,
        start_time INTEGER,
        message_id INTEGER,
        chat_id INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_clicks (
        user_id INTEGER,
        clicks INTEGER DEFAULT 0,
        username TEXT,
        PRIMARY KEY (user_id)
    )
''')
conn.commit()

# Переменные челленджа
last_update_time: int = 0
challenge_active = False
challenge_start_time: int = 0
challenge_message_id = 0
challenge_chat_id = 0
plot_messages_sent = set()
last_message_text = ""
last_time_left = ""  # Для отслеживания изменения времени
last_click_time: int = 0  # Для отслеживания времени последнего клика
user_last_click = defaultdict(float)  # Для антиспама
user_remaining_clicks = defaultdict(int)  # Локальный счётчик оставшихся кликов

# Переменная для приза
PRIZE_MESSAGE = "🏆С победителем скоро свяжуться в ЛС! Или я или табуреты!\n"  # noqa

def load_challenge_data():
    global challenge_start_time, challenge_message_id, challenge_chat_id, challenge_active
    cursor.execute("SELECT clicks, start_time, message_id, chat_id FROM challenge WHERE id = 1")
    result = cursor.fetchone()
    if result:
        clicks, start_time, message_id, chat_id = result
        challenge_start_time = int(start_time) if start_time else 0
        challenge_message_id = int(message_id) if message_id else 0
        challenge_chat_id = int(chat_id) if chat_id else ALLOWED_CHAT_ID
        challenge_active = challenge_start_time and (time.time() - challenge_start_time) < CHALLENGE_DURATION
        return clicks
    return 0

def save_challenge_data(clicks):
    cursor.execute('''
        INSERT OR REPLACE INTO challenge (id, clicks, start_time, message_id, chat_id)
        VALUES (1, ?, ?, ?, ?)
    ''', (clicks, challenge_start_time, challenge_message_id, challenge_chat_id))
    conn.commit()

def load_user_clicks():
    user_clicks = defaultdict(int)
    user_names = {}
    cursor.execute("SELECT user_id, clicks, username FROM user_clicks")
    for user_id, clicks, username in cursor.fetchall():
        user_clicks[user_id] = clicks
        user_names[user_id] = username if username else f"Пользователь {user_id}"  # noqa
    return user_clicks, user_names


def save_user_click(user_id, clicks, username):
    cursor.execute('''
        INSERT OR REPLACE INTO user_clicks (user_id, clicks, username)
        VALUES (?, ?, ?)
    ''', (user_id, clicks, username))
    conn.commit()

def get_time_left():
    if not challenge_start_time or challenge_start_time <= 0:
        return "0 часов 0 минут"  # noqa
    elapsed_time = time.time() - challenge_start_time
    remaining_time = max(0, CHALLENGE_DURATION - elapsed_time)
    if remaining_time <= 0:
        return "0 часов 0 минут"  # noqa
    hours = int(remaining_time // 3600)
    minutes = int((remaining_time % 3600) // 60)
    return f"{hours} часов {minutes} минут"  # noqa

def get_leaderboard(user_clicks, user_names):
    if not user_clicks:
        return "Никто не участвовал! 🪑"  # noqa
    sorted_users = sorted(user_clicks.items(), key=lambda x: x[1], reverse=True)[:3]
    return "\n".join(f"{i}. {user_names[user_id]}: {count} кликов"  # noqa
                     for i, (user_id, count) in enumerate(sorted_users, 1))

def get_leader(user_clicks, user_names):
    if not user_clicks:
        return "Никто", 0  # noqa
    leader_id, leader_clicks = max(user_clicks.items(), key=lambda x: x[1])
    return user_names[leader_id], leader_clicks

async def end_challenge(winner: str):
    global challenge_active
    challenge_active = False

    user_clicks, user_names = load_user_clicks()
    leader_name, leader_clicks = get_leader(user_clicks, user_names)

    message = (
        "🎉 УРА! Вы спасли канал от табуретов с Кифируна! 🪑\n"
        "Их корабль повержен, а они возвращаются на свою планету в спасательных капсулах!\n"  # noqa
        f"Вы набрали {TOTAL_CLICKS_NEEDED} кликов! 🚀\n"
        "🏆 Таблица лидеров (Топ-3):\n" + get_leaderboard(user_clicks, user_names) +
        f"\nБольше всех кликов ({leader_clicks}) сделал {leader_name}!\n"  # noqa
        f"{PRIZE_MESSAGE}\n"
        "С 1 апреля, друзья! 😄"  # noqa
    ) if winner == "users" else (
        "⏰ Время истекло! 😱\n"
        "Вы не успели... Канал захвачен и будут вести табуреты с Кифируна! 🪑\n"
        "По крайней мере пока меня утащили на их планету и ставят опыты.\n"  # noqa
        "🏆 Таблица лидеров (Топ-3):\n" + get_leaderboard(user_clicks, user_names) +
        f"\nБольше всех кликов ({leader_clicks}) сделал {leader_name}!\n"  # noqa
        f"{PRIZE_MESSAGE}\n"
        "С 1 апреля, друзья! 😄"  # noqa
    )

    await bot.edit_message_text(
        chat_id=challenge_chat_id,
        message_id=challenge_message_id,
        text=message
    )

async def notify_time_left():
    while challenge_active:
        elapsed_time = time.time() - challenge_start_time
        remaining_time = CHALLENGE_DURATION - elapsed_time

        if not challenge_active:
            break

        if 3595 <= remaining_time <= 3605:
            await bot.send_message(chat_id=challenge_chat_id, text="⏰ Остался 1 час! Кликайте быстрее! 🪑")  # noqa
        elif 1795 <= remaining_time <= 1805:
            await bot.send_message(chat_id=challenge_chat_id, text="⏰ Осталось 30 минут! Спасите канал! 🪑")  # noqa
        elif 295 <= remaining_time <= 305:
            await bot.send_message(chat_id=challenge_chat_id, text="⏰ 5 минут до конца! Последний шанс! 🪑")  # noqa

        if remaining_time <= 0:
            await end_challenge("taburets")
            break

        await asyncio.sleep(60)

async def update_message():
    global last_update_time, last_message_text, last_time_left, last_click_time
    while challenge_active:
        current_time = int(time.time())
        time_left = get_time_left()
        clicks = load_challenge_data()


        # Проверяем, нужно ли обновлять сообщение
        should_update = False
        if current_time - last_click_time < UPDATE_INTERVAL:
            # Если были клики недавно, обновляем раз в 5 секунд
            if current_time - last_update_time >= UPDATE_INTERVAL:
                should_update = True
        else:
            # Если кликов не было, обновляем только при изменении времени
            if time_left != last_time_left:
                should_update = True

        if should_update:
            message_text = (
                "🚨 ВНИМАНИЕ! 1 апреля табуреты с Кифируна захватили канал! 🪑\n"  # noqa
                "Нужно 10,000 кликов, чтобы спасти его! 🆘\n"
                "Не успеете — табуреты захватят канал, а меня утащат на Кифирун ставить опыты! 😱\n"  # noqa
                "Кликайте! 👀⏰\n\n"
                f"Время до конца: {time_left}\n"
                f"Осталось кликов: {TOTAL_CLICKS_NEEDED - clicks}"
            )

            if message_text != last_message_text:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Нажми, чтобы спасти канал! 🪑", callback_data='save_channel')]  # noqa
                ])
                try:
                    await bot.edit_message_text(
                        chat_id=challenge_chat_id,
                        message_id=challenge_message_id,
                        text=message_text,
                        reply_markup=keyboard
                    )
                    last_message_text = message_text
                    last_update_time = current_time
                    last_time_left = time_left
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                    await bot.edit_message_text(
                        chat_id=challenge_chat_id,
                        message_id=challenge_message_id,
                        text=message_text,
                        reply_markup=keyboard
                    )
                    last_message_text = message_text
                    last_update_time = current_time
                    last_time_left = time_left
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e):
                        last_message_text = message_text
                        last_update_time = current_time
                        last_time_left = time_left

        await asyncio.sleep(1)  # Проверяем каждую секунду

@router.message(Command("start_challenge"))
async def start_challenge(message: Message):
    global challenge_start_time, challenge_message_id, challenge_chat_id, challenge_active, last_update_time, plot_messages_sent, last_message_text, last_time_left, last_click_time

    if message.chat.type != "private":
        await message.reply("Эта команда работает только в ЛС! 🪑")  # noqa
        return

    admins = await bot.get_chat_administrators(ALLOWED_CHAT_ID)
    if message.from_user.id not in [admin.user.id for admin in admins]:
        await message.reply("Эта команда только для админов канала! 🪑")  # noqa
        return

    if challenge_active:
        await message.reply("Челлендж уже идёт! Кликайте на кнопку в канале! 🪑")  # noqa
        return

    challenge_start_time = int(time.time())
    challenge_chat_id = ALLOWED_CHAT_ID
    challenge_active = True
    last_update_time = 0
    last_click_time = 0
    plot_messages_sent.clear()
    last_message_text = ""
    last_time_left = ""
    user_last_click.clear()
    user_remaining_clicks.clear()

    cursor.execute("DELETE FROM challenge")
    cursor.execute("DELETE FROM user_clicks")
    conn.commit()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Нажми, чтобы спасти канал! 🪑", callback_data='save_channel')]  # noqa
    ])


    time_left = get_time_left()
    message_text = (
        "🚨 ВНИМАНИЕ! 1 апреля табуреты с Кифируна захватили канал! 🪑\n"  # noqa
        "Нужно 10,000 кликов, чтобы спасти его! 🆘\n"
        "Не успеете — табуреты захватят канал, а меня утащат на Кифирун ставить опыты! 😱\n"  # noqa
        "Кликайте! 👀⏰\n\n"
        f"Время до конца: {time_left}\n"
        f"Осталось кликов: {TOTAL_CLICKS_NEEDED}"
    )

    msg = await bot.send_message(
        chat_id=challenge_chat_id,
        text=message_text,
        reply_markup=keyboard
    )
    challenge_message_id = msg.message_id
    last_message_text = message_text
    last_time_left = time_left

    save_challenge_data(0)
    asyncio.create_task(notify_time_left())
    asyncio.create_task(update_message())
    await message.reply("Челлендж начался! Сообщение отправлено в канал. 🪑")  # noqa

@router.message(Command("force_good_end"))
async def force_good_end(message: Message):
    if message.chat.type != "private":
        await message.reply("Эта команда работает только в ЛС! 🪑")  # noqa
        return

    admins = await bot.get_chat_administrators(ALLOWED_CHAT_ID)
    if message.from_user.id not in [admin.user.id for admin in admins]:
        await message.reply("Только для админов канала! 🪑")  # noqa
        return

    if not challenge_active:
        await message.reply("Челлендж не активен! Запустите /start_challenge 🪑")  # noqa
        return

    await end_challenge("users")
    await message.reply("Челлендж завершён! Подписчики победили! 🎉")  # noqa

@router.message(Command("force_bad_end"))
async def force_bad_end(message: Message):
    if message.chat.type != "private":
        await message.reply("Эта команда работает только в ЛС! 🪑")  # noqa
        return

    admins = await bot.get_chat_administrators(ALLOWED_CHAT_ID)
    if message.from_user.id not in [admin.user.id for admin in admins]:
        await message.reply("Только для админов канала! 🪑")  # noqa
        return

    if not challenge_active:
        await message.reply("Челлендж не активен! Запустите /start_challenge 🪑")  # noqa
        return

    await end_challenge("taburets")
    await message.reply("Челлендж завершён! Табуреты победили! 😱")  # noqa

@router.callback_query(F.data == "save_channel")
async def button_click(callback: CallbackQuery):
    global challenge_active, last_click_time

    if not challenge_active:
        await callback.answer("Челлендж не активен!", show_alert=True)
        return

    user_id = callback.from_user.id
    current_time = time.time()

    # Антиспам: проверка времени последнего клика
    if current_time - user_last_click[user_id] < CLICK_COOLDOWN:
        await callback.answer("Слишком быстро! Подожди немного 🪑", show_alert=False)
        return

    user_last_click[user_id] = current_time
    last_click_time = int(current_time)

    # Получаем актуальное количество кликов
    actual_clicks = load_challenge_data()
    
    # Обновляем локальный счетчик пользователя до актуального значения
    user_remaining_clicks[user_id] = TOTAL_CLICKS_NEEDED - actual_clicks

    user_name = callback.from_user.username or callback.from_user.first_name or f"Пользователь {user_id}"

    # Увеличиваем общее количество кликов
    clicks = actual_clicks + 1
    user_clicks, user_names = load_user_clicks()
    user_clicks[user_id] += 1

    # Уменьшаем локальный счётчик пользователя
    user_remaining_clicks[user_id] -= 1
    if user_remaining_clicks[user_id] < 0:
        user_remaining_clicks[user_id] = 0

    # Обработка сюжетных событий
    for threshold, plot_message in PLOT_THRESHOLDS.items():
        if clicks == threshold and threshold not in plot_messages_sent:
            await bot.send_message(chat_id=challenge_chat_id, text=plot_message)
            plot_messages_sent.add(threshold)

    save_user_click(user_id, user_clicks[user_id], user_name)
    save_challenge_data(clicks)

    if clicks >= TOTAL_CLICKS_NEEDED:
        await end_challenge("users")
        return


    await callback.answer(f"Клик засчитан! Осталось кликов: {user_remaining_clicks[user_id]} 🪑", show_alert=False)

async def main():
    global challenge_active
    # Не продолжаем челлендж автоматически при запуске
    challenge_active = False

    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)
    conn.close()

if __name__ == '__main__':
    asyncio.run(main())
