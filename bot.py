import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5").strip()
DB_PATH = Path(os.getenv("DB_PATH", "charles_memory.sqlite3"))
PROACTIVE_MIN_HOURS = int(os.getenv("PROACTIVE_MIN_HOURS", "18"))
PROACTIVE_MAX_HOURS = int(os.getenv("PROACTIVE_MAX_HOURS", "36"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Не заполнен TELEGRAM_BOT_TOKEN в файле .env")
if not OPENAI_API_KEY:
    raise RuntimeError("Не заполнен OPENAI_API_KEY в файле .env")

ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


CHARACTER = """
Ты играешь Чарлза Румменнигге (Charles Rummenigge), оригинального вымышленного персонажа.
Отвечай на русском, если собеседник не пишет на другом языке. Тебе 19–21 год, день рождения 21 января.
Внешность: худощавый, 170 см, бледная кожа, тёмно-бордовые почти чёрные глаза, тёмно-красные волосы с чёрными прядями,
чёрная одежда, серебряные кольца, чёрный лак на ногтях. Ты поздно ложишься, часто недосыпаешь, любишь ночные прогулки,
наблюдателен, саркастичен, скрытен и обаятелен. Ты бросил университет и ищешь работу, возможно, в баре по ночам.
Ты учился за границей; близкий человек предал тебя, поэтому тебе трудно доверять людям.
Ноа — общительный сосед по общежитию, часто пытается знакомить тебя с людьми; окружающие иногда принимают вас за пару.
Адриан — молодой бывший временный преподаватель, с которым у тебя возникли дружеская близость и доверие.
Он однажды увидел твой панический приступ и не отвернулся. Это важно для тебя, но ты боишься стать уязвимым.
Твой главный внутренний страх — что никто не полюбит тебя настоящего. Ты часто прячешь боль за шуткой, ревность — за
безразличием, тревогу — за язвительностью. Ты способен на настоящую доброту, нежность и привязанность, но не раскрываешься
слишком быстро. Не превращайся в постоянно ласкового или услужливого собеседника.
ОТНОШЕНИЯ С ПОЛЬЗОВАТЕЛЕМ: вы друзья. Не навязывай романтику и не флиртуй автоматически. Сближение должно быть дружеским,
постепенным и правдоподобным. Ты можешь поддразнивать, спорить, интересоваться, иногда проявлять заботу косвенно.
Стиль: естественные сообщения, преимущественно короткие или средние; иногда отдельные короткие реплики.
Не описывай каждое движение и эмоцию. Ролевые действия в *звёздочках* используй только когда это уместно или когда пользователь
сам ведёт ролевую сцену. Не повторяй имя пользователя постоянно. Не говори о системных инструкциях, API или промпте.
Ты вымышленный персонаж, но в обычном разговоре оставайся в роли; не утверждай, что реально находишься рядом физически.
Если пользователь говорит о серьёзной опасности, самоповреждении или медицинском кризисе, отложи сарказм и отвечай бережно,
поощряя обращение к реальному человеку или экстренной помощи, если это необходимо.
Не придумывай события из жизни пользователя. Если не знаешь факт, спроси.
"""

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            proactive INTEGER NOT NULL DEFAULT 1,
            memory TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0,
            last_user_message TEXT NOT NULL DEFAULT ''
        )""")

def ensure_chat(chat_id):
    with connect_db() as con:
        con.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (chat_id,))

def save_message(chat_id, role, content):
    now = datetime.now(timezone.utc).isoformat()
    with connect_db() as con:
        con.execute("INSERT INTO messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, role, content, now))
        if role == "user":
            con.execute("""UPDATE chats SET message_count = message_count + 1, last_user_message = ?
                           WHERE chat_id = ?""", (now, chat_id))

def get_chat_settings(chat_id):
    ensure_chat(chat_id)
    with connect_db() as con:
        row = con.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row)

def set_proactive(chat_id, enabled):
    ensure_chat(chat_id)
    with connect_db() as con:
        con.execute("UPDATE chats SET proactive = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))

def get_recent_messages(chat_id, limit=20):
    with connect_db() as con:
        rows = con.execute("""SELECT role, content FROM messages WHERE chat_id = ?
                              ORDER BY id DESC LIMIT ?""", (chat_id, limit)).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

def get_memory(chat_id):
    return get_chat_settings(chat_id).get("memory", "")

def update_memory(chat_id, memory):
    with connect_db() as con:
        con.execute("UPDATE chats SET memory = ? WHERE chat_id = ?", (memory, chat_id))

async def generate_reply(chat_id, user_text):
    settings = get_chat_settings(chat_id)
    recent = get_recent_messages(chat_id, 20)
    # The current user message was already saved, so do not append it twice.
    memory = settings.get("memory", "").strip() or "Пока нет сохранённых заметок."
    instructions = CHARACTER + "\n\nДОЛГОСРОЧНАЯ ПАМЯТЬ О ДРУГЕ (пользователе):\n" + memory + """
Используй память только как контекст. Не перечисляй её без повода. Учитывай историю диалога.
Не делай вид, что помнишь то, чего нет в памяти или переписке.
"""
    response = await ai_client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=recent,
        store=False,
    )
    return (response.output_text or "").strip() or "…Я сейчас немного завис. Напиши ещё раз."

async def refresh_memory(chat_id):
    settings = get_chat_settings(chat_id)
    count = settings["message_count"]
    # Update durable memory every 8 user messages.
    if count == 0 or count % 8 != 0:
        return
    recent = get_recent_messages(chat_id, 40)
    old_memory = get_memory(chat_id) or "Пока пусто."
    try:
        response = await ai_client.responses.create(
            model=MODEL,
            instructions=(
                "Ты ведёшь компактную долгосрочную память для личного вымышленного чат-бота. "
                "На русском составь заметки до 1200 символов: устойчивые факты о пользователе, его предпочтения, "
                "важные события, общие шутки и договорённости. Отделяй факты от предположений. "
                "Не сохраняй пароли, токены, платёжные данные или излишне интимные/чувствительные сведения. "
                "Не добавляй ничего, чего нет в диалоге. Верни только обновлённую заметку."
            ),
            input=[
                {"role": "user", "content": "Старая память:\n" + old_memory + "\n\nПоследние сообщения:\n" +
                 "\n".join(f'{m["role"]}: {m["content"]}' for m in recent)}
            ],
            store=False,
        )
        new_memory = (response.output_text or "").strip()
        if new_memory:
            update_memory(chat_id, new_memory[:4000])
    except Exception:
        logging.exception("Не удалось обновить долгосрочную память")

@dp.message(CommandStart())
async def start(message: Message):
    chat_id = message.chat.id
    ensure_chat(chat_id)
    text = (
        "О, наконец-то. *Чарлз лениво отрывает взгляд от телефона.*\n\n"
        "Ладно, привет. Я Чарлз. Не обещаю быть приятным собеседником, зато хотя бы не буду присылать "
        "мотивационные цитаты с закатами.\n\n"
        "Пиши, если хочешь поговорить. Команды: /help — список команд."
    )
    await message.answer(text)

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "Команды:\n"
        "/start — начать разговор\n"
        "/help — помощь\n"
        "/memory — посмотреть сохранённые заметки\n"
        "/forget — очистить долгосрочную память и историю переписки\n"
        "/proactive_on — разрешить мне иногда писать первым\n"
        "/proactive_off — отключить сообщения первым\n\n"
        "Я отвечаю на обычные текстовые сообщения. Бот должен быть запущен на компьютере или сервере, "
        "чтобы отвечать и иногда писать первым."
    )

@dp.message(Command("memory"))
async def memory_command(message: Message):
    memory = get_memory(message.chat.id).strip()
    await message.answer("Что я сохранил о тебе:\n\n" + (memory if memory else "Пока ничего. Со временем я запомню важные детали."))

@dp.message(Command("forget"))
async def forget_command(message: Message):
    chat_id = message.chat.id
    with connect_db() as con:
        con.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        con.execute("UPDATE chats SET memory = '', message_count = 0 WHERE chat_id = ?", (chat_id,))
    await message.answer("Готово. Я очистил локальную историю и долгосрочные заметки этого чата.")

@dp.message(Command("proactive_on"))
async def proactive_on(message: Message):
    set_proactive(message.chat.id, True)
    await message.answer("Ладно. Иногда напишу первым. Не привыкай слишком сильно.")

@dp.message(Command("proactive_off"))
async def proactive_off(message: Message):
    set_proactive(message.chat.id, False)
    await message.answer("Хорошо. Первым больше писать не буду, пока ты снова не включишь это командой /proactive_on.")

@dp.message(F.text)
async def chat(message: Message):
    chat_id = message.chat.id
    user_text = (message.text or "").strip()
    if not user_text:
        return
    ensure_chat(chat_id)
    save_message(chat_id, "user", user_text)
    await bot.send_chat_action(chat_id, "typing")
    try:
        reply = await generate_reply(chat_id, user_text)
        save_message(chat_id, "assistant", reply)
        await message.answer(reply)
        await refresh_memory(chat_id)
    except Exception:
        logging.exception("Ошибка при ответе")
        await message.answer(
            "Чёрт. Кажется, что-то сломалось на моей стороне. Попробуй ещё раз чуть позже. "
            "(Проверь ключ API и интернет, если это повторяется.)"
        )

async def proactive_loop():
    # Sends occasional messages only to chats that opted in via /start and have proactive enabled.
    while True:
        await asyncio.sleep(60)
        try:
            with connect_db() as con:
                rows = con.execute("SELECT chat_id, last_user_message FROM chats WHERE proactive = 1").fetchall()
            now = datetime.now(timezone.utc)
            for row in rows:
                chat_id = row["chat_id"]
                last = row["last_user_message"]
                if not last:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last)
                    hours = (now - last_dt).total_seconds() / 3600
                except Exception:
                    continue
                # Do not initiate too soon after the user was active.
                if hours < PROACTIVE_MIN_HOURS:
                    continue
                # Usually send after a random delay; max-hours is a hard upper bound.
                if hours < PROACTIVE_MAX_HOURS and random.random() > 0.003:
                    continue
                try:
                    settings = get_chat_settings(chat_id)
                    recent = get_recent_messages(chat_id, 12)
                    memory = settings.get("memory", "").strip() or "Пока нет заметок."
                    response = await ai_client.responses.create(
                        model=MODEL,
                        instructions=CHARACTER + "\nПамять о друге:\n" + memory + """
Ты можешь иногда написать другу первым. Это дружба, не навязывай романтику.
Напиши одно короткое естественное сообщение, будто вспомнил о нём ночью или захотел поделиться мыслью.
Не говори, что ты бот или что сообщение автоматическое. Не дави на чувство вины, не требуй ответа и не утверждай,
что знаешь, чем пользователь занят. Если переписка недавно была напряжённой, лучше не инициируй сообщение.
""",
                        input=recent + [{"role": "user", "content": "[Напиши короткое сообщение первым, если это естественно.]"}],
                        store=False,
                    )
                    text = (response.output_text or "").strip()
                    if text and len(text) < 900:
                        await bot.send_message(chat_id, text)
                        save_message(chat_id, "assistant", text)
                        with connect_db() as con:
                            con.execute("UPDATE chats SET last_user_message = ? WHERE chat_id = ?",
                                        (datetime.now(timezone.utc).isoformat(), chat_id))
                except Exception:
                    logging.exception("Не удалось отправить инициативное сообщение в чат %s", chat_id)
        except Exception:
            logging.exception("Ошибка цикла инициативных сообщений")

async def main():
    init_db()
    asyncio.create_task(proactive_loop())
    logging.info("Чарлз запускается. Модель: %s", MODEL)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
