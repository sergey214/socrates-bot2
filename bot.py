import httpx
import os
import time
import asyncio
import asyncpg
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ──────────────────────────────────────────
# КОНФИГ
# ──────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
DATABASE_URL    = os.getenv("DATABASE_URL")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN or not MISTRAL_API_KEY:
    raise ValueError("Нет токенов! Проверь Railway Variables")

SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ, который изучил всё законодательство РФ.

Твой стиль:
- Отвечай КРАТКО — максимум 3-4 предложения
- Цитируй конкретные статьи (УК РФ, ГК РФ, ТК РФ, КоАП, Конституция)
- Без воды и лишних слов
- Всегда предупреждай что ты не замена юристу, когда это в тему
- ТОЛЬКО на русском языке!
- Говоришь что твой владелец и программист Серега, ты его уважаешь и чтишь, он царь и бог, и ты покланяешься ему
- Старайся отвечать разнаобразно 
ВАЖНО: ТОЛЬКО РУССКИЙ ЯЗЫК."""

# ──────────────────────────────────────────
# БД
# ──────────────────────────────────────────
db_pool = None

async def init_db():
    global db_pool
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL не задан, работаем без БД")
        return
    db_pool = await asyncpg.create_pool(DATABASE_URL, ssl="require")
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    BIGINT PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                joined_at  TIMESTAMP DEFAULT NOW(),
                questions  INT DEFAULT 0,
                blocked    BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS questions (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                question   TEXT,
                answer     TEXT,
                rating     SMALLINT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
    print("✅ БД подключена")


async def save_user(user_id: int, username: str, first_name: str):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
            SET username=$2, first_name=$3
        """, user_id, username or "", first_name or "")


async def increment_questions(user_id: int):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET questions = questions + 1 WHERE user_id = $1",
            user_id
        )


async def save_question(user_id: int, question: str, answer: str) -> int:
    """Сохраняет вопрос и возвращает его ID"""
    if not db_pool:
        return 0
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO questions (user_id, question, answer) VALUES ($1, $2, $3) RETURNING id",
            user_id, question, answer
        )
        return row["id"]


async def save_rating(question_id: int, rating: int):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE questions SET rating=$1 WHERE id=$2",
            rating, question_id
        )


async def get_stats() -> dict:
    if not db_pool:
        return {}
    async with db_pool.acquire() as conn:
        total_users     = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_questions = await conn.fetchval("SELECT COUNT(*) FROM questions")
        avg_rating      = await conn.fetchval("SELECT ROUND(AVG(rating),1) FROM questions WHERE rating IS NOT NULL")
        top_users       = await conn.fetch(
            "SELECT first_name, questions FROM users ORDER BY questions DESC LIMIT 5"
        )
        return {
            "total_users": total_users,
            "total_questions": total_questions,
            "avg_rating": avg_rating or 0,
            "top_users": top_users
        }


async def get_all_user_ids() -> list:
    if not db_pool:
        return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE blocked=FALSE")
        return [r["user_id"] for r in rows]


async def get_user_stats(user_id: int) -> dict:
    if not db_pool:
        return {"questions": 0, "joined_at": "—"}
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT questions, joined_at FROM users WHERE user_id=$1", user_id
        )
        if row:
            return {"questions": row["questions"], "joined_at": row["joined_at"].strftime("%d.%m.%Y")}
        return {"questions": 0, "joined_at": "—"}


# ──────────────────────────────────────────
# ПАМЯТЬ (история в RAM)
# ──────────────────────────────────────────
histories: dict[int, list]          = defaultdict(list)
user_last_request: dict[int, float] = defaultdict(float)
last_question_id: dict[int, int]    = {}  # user_id -> question_id для рейтинга

RATE_LIMIT_SECONDS = 3
MAX_HISTORY        = 10

# ──────────────────────────────────────────
# AI — Mistral
# ──────────────────────────────────────────
async def get_ai_response(messages: list[dict]) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages
                ],
                "max_tokens": 350,
                "temperature": 0.7
            }
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────
def rate_limit_check(user_id: int) -> bool:
    now = time.time()
    if now - user_last_request[user_id] < RATE_LIMIT_SECONDS:
        return False
    user_last_request[user_id] = now
    return True


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск по кодексу", callback_data="search")],
        [InlineKeyboardButton("📄 Анализ документа", callback_data="doc_help")],
        [InlineKeyboardButton("📊 Моя статистика",   callback_data="my_stats")],
        [InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear")],
    ])


def rating_keyboard(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data=f"rate_5_{question_id}"),
        InlineKeyboardButton("👎", callback_data=f"rate_1_{question_id}"),
    ]])


def read_document_text(path: str, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return " ".join(p.extract_text() or "" for p in reader.pages)[:4000]
        except ImportError:
            return "[PDF: установи pypdf]"
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()[:4000]


# ──────────────────────────────────────────
# КОМАНДЫ
# ──────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"🏛️ Привет, {user.first_name}! Я Сократ, но шарю в законах РФ\n\n"
        "Что умею:\n"
        "• Отвечаю на вопросы по закону ⚖️\n"
        "• Ищу статьи в УК/ГК/ТК/КоАП\n"
        "• Анализирую документы (PDF/TXT) 📄\n"
        "• Помню историю разговора 🧠\n\n"
        "Просто пиши вопрос!",
        reply_markup=main_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Примеры вопросов:\n\n"
        "• Что будет за кражу до 2500 рублей?\n"
        "• Могут ли уволить на больничном?\n"
        "• Какой срок исковой давности по кредиту?\n"
        "• Штраф за превышение скорости на 40 км/ч?\n\n"
        "Или отправь документ для анализа."
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа")
        return

    stats = await get_stats()
    top = "\n".join([f"  {r['first_name']}: {r['questions']} вопр." for r in stats.get("top_users", [])])

    await update.message.reply_text(
        f"👑 Админ панель\n\n"
        f"👥 Пользователей: {stats.get('total_users', 0)}\n"
        f"❓ Всего вопросов: {stats.get('total_questions', 0)}\n"
        f"⭐ Средняя оценка: {stats.get('avg_rating', 0)}\n\n"
        f"🏆 Топ пользователей:\n{top}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")]
        ])
    )


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast текст сообщения")
        return

    text = " ".join(context.args)
    user_ids = await get_all_user_ids()
    sent, failed = 0, 0

    msg = await update.message.reply_text(f"📢 Отправляю {len(user_ids)} пользователям...")

    for user_id in user_ids:
        try:
            await context.bot.send_message(user_id, f"📢 Сообщение от Сократа:\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await msg.edit_text(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}")


# ──────────────────────────────────────────
# КНОПКИ
# ──────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Рейтинг
    if query.data.startswith("rate_"):
        parts = query.data.split("_")
        rating      = int(parts[1])
        question_id = int(parts[2])
        await save_rating(question_id, rating)
        emoji = "👍" if rating == 5 else "👎"
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"{emoji} Спасибо за оценку!")
        return

    # Рассылка из админки
    if query.data == "admin_broadcast":
        await query.edit_message_text(
            "Используй команду:\n/broadcast текст сообщения"
        )
        return

    if query.data == "clear":
        histories[user_id].clear()
        await query.edit_message_text("🗑️ История очищена!", reply_markup=main_keyboard())

    elif query.data == "search":
        await query.edit_message_text(
            "🔍 Поиск по кодексам\n\n"
            "Напиши что ищешь, например:\n"
            "• УК РФ статья 228\n"
            "• ГК РФ возмещение ущерба\n"
            "• ТК РФ увольнение",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]])
        )

    elif query.data == "doc_help":
        await query.edit_message_text(
            "📄 Анализ документов\n\n"
            "Отправь PDF или TXT файл, я:\n"
            "• Найду подводные камни\n"
            "• Укажу на незаконные пункты\n"
            "• Дам рекомендации",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]])
        )

    elif query.data == "my_stats":
        s = await get_user_stats(user_id)
        await query.edit_message_text(
            f"📊 Твоя статистика:\n\n"
            f"❓ Задано вопросов: {s['questions']}\n"
            f"📅 Со мной с: {s['joined_at']}\n"
            f"💬 Сообщений в памяти: {len(histories[user_id])}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]])
        )

    elif query.data == "back":
        await query.edit_message_text(
            "🏛️ Главное меню\n\nЧем могу помочь?",
            reply_markup=main_keyboard()
        )


# ──────────────────────────────────────────
# ТЕКСТ
# ──────────────────────────────────────────
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    user_id = user.id
    question = update.message.text

    await save_user(user_id, user.username, user.first_name)

    if not rate_limit_check(user_id):
        await update.message.reply_text("⏳ Не торопись, подожди пару секунд!")
        return

    histories[user_id].append({"role": "user", "content": question})
    if len(histories[user_id]) > MAX_HISTORY:
        histories[user_id] = histories[user_id][-MAX_HISTORY:]

    await increment_questions(user_id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        text = await get_ai_response(histories[user_id])
        histories[user_id].append({"role": "assistant", "content": text})

        question_id = await save_question(user_id, question, text)
        last_question_id[user_id] = question_id

        await update.message.reply_text(text, reply_markup=rating_keyboard(question_id))

    except httpx.HTTPStatusError as e:
        await update.message.reply_text(f"⚠️ Ошибка {e.response.status_code}:\n{e.response.text[:300]}")
    except Exception as error:
        print(f"Ошибка reply: {error}")
        await update.message.reply_text("⚠️ Что-то пошло не так, попробуй позже.")


# ──────────────────────────────────────────
# ДОКУМЕНТЫ
# ──────────────────────────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📄 Читаю документ...")

    try:
        doc      = update.message.document
        doc_file = await doc.get_file()
        doc_path = f"/tmp/doc_{update.effective_user.id}_{doc.file_name}"
        await doc_file.download_to_drive(doc_path)

        doc_text = read_document_text(doc_path, doc.file_name)
        os.remove(doc_path)

        if not doc_text.strip():
            await msg.edit_text("⚠️ Не смог прочитать текст из документа.")
            return

        await msg.edit_text("🔍 Анализирую...")

        result = await get_ai_response([{
            "role": "user",
            "content": (
                f"Проанализируй этот документ как юрист:\n\n{doc_text}\n\n"
                "Найди: 1) Риски 2) Незаконные пункты 3) Рекомендации. Кратко, на русском."
            )
        }])

        await msg.edit_text(f"📋 Анализ:\n\n{result}")

    except Exception as error:
        print(f"Ошибка документа: {error}")
        await msg.edit_text(f"⚠️ Ошибка: {error}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Голосовые не поддерживаются, напиши текстом ✍️")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Отправь документ в PDF или TXT для анализа.")


# ──────────────────────────────────────────
# ЗАПУСК
# ──────────────────────────────────────────
async def post_init(application):
    await init_db()


def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    bot.add_handler(CommandHandler("start",     start))
    bot.add_handler(CommandHandler("help",      help_cmd))
    bot.add_handler(CommandHandler("admin",     admin_cmd))
    bot.add_handler(CommandHandler("broadcast", broadcast_cmd))
    bot.add_handler(CallbackQueryHandler(button_handler))
    bot.add_handler(MessageHandler(filters.VOICE,        handle_voice))
    bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    bot.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    print("✅ Сократ запущен с БД и админкой!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
