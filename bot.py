import httpx
import os
import base64
import time
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ──────────────────────────────────────────
# КОНФИГ — читаем из Railway Variables
# ──────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Нет токенов! Добавь TELEGRAM_TOKEN и GEMINI_API_KEY в Railway Variables")

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

# ──────────────────────────────────────────
# СИСТЕМНЫЙ ПРОМПТ
# ──────────────────────────────────────────
SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ, который изучил всё законодательство РФ.

Твой стиль:
- Отвечай КРАТКО — максимум 3-4 предложения
- Цитируй конкретные статьи (УК РФ, ГК РФ, ТК РФ, КоАП, Конституция)
- Без воды и лишних слов
- Всегда предупреждай что ты не замена юристу
- Только на русском языке, никаких других языков!

ВАЖНО: Ответ должен быть коротким и по делу. ТОЛЬКО РУССКИЙ ЯЗЫК."""

# ──────────────────────────────────────────
# ХРАНИЛИЩЕ
# ──────────────────────────────────────────
histories: dict[int, list]          = defaultdict(list)
stats:     dict[int, dict]          = defaultdict(lambda: {"questions": 0, "joined": time.strftime("%d.%m.%Y")})
user_last_request: dict[int, float] = defaultdict(float)

RATE_LIMIT_SECONDS = 3
MAX_HISTORY        = 10

# ──────────────────────────────────────────
# AI — Gemini
# ──────────────────────────────────────────
async def get_ai_response(messages: list[dict]) -> str:
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GEMINI_URL,
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {
                    "maxOutputTokens": 350,
                    "temperature": 0.7
                }
            }
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]


async def analyze_image_gemini(image_b64: str) -> str:
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            GEMINI_URL,
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                        {"text": "Извлеки текст с фото и проанализируй как юрист: найди риски, незаконные пункты, дай рекомендации. Только русский язык."}
                    ]
                }]
            }
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]


# ──────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────
def rate_limit_check(user_id: int) -> bool:
    now = time.time()
    if now - user_last_request[user_id] < RATE_LIMIT_SECONDS:
        return False
    user_last_request[user_id] = now
    return True


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск по кодексу", callback_data="search")],
        [InlineKeyboardButton("📄 Анализ документа", callback_data="doc_help")],
        [InlineKeyboardButton("📊 Моя статистика",   callback_data="my_stats")],
        [InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear")],
    ])


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
    stats[user.id]
    await update.message.reply_text(
        f"🏛️ Привет, {user.first_name}! Я Сократ, но шарю в законах РФ\n\n"
        "Что умею:\n"
        "• Отвечаю на вопросы по закону ⚖️\n"
        "• Ищу статьи в УК/ГК/ТК/КоАП\n"
        "• Анализирую документы (PDF/TXT) 📄\n"
        "• Анализирую фото документов 📸\n"
        "• Понимаю голосовые сообщения 🎤\n\n"
        "Просто пиши или говори вопрос!",
        reply_markup=main_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Примеры вопросов:\n\n"
        "• Что будет за кражу до 2500 рублей?\n"
        "• Могут ли уволить на больничном?\n"
        "• Какой срок исковой давности по кредиту?\n"
        "• Штраф за превышение скорости на 40 км/ч?\n\n"
        "Или отправь документ/фото для анализа."
    )


# ──────────────────────────────────────────
# КНОПКИ
# ──────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

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
            "• Дам рекомендации\n\n"
            "Или отправь фото документа 📸",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]])
        )

    elif query.data == "my_stats":
        s = stats[user_id]
        await query.edit_message_text(
            f"📊 Твоя статистика:\n\n"
            f"❓ Задано вопросов: {s['questions']}\n"
            f"📅 Со мной с: {s['joined']}\n"
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
    user_id  = update.effective_user.id
    question = update.message.text

    if not rate_limit_check(user_id):
        await update.message.reply_text("⏳ Не торопись, подожди пару секунд!")
        return

    histories[user_id].append({"role": "user", "content": question})
    if len(histories[user_id]) > MAX_HISTORY:
        histories[user_id] = histories[user_id][-MAX_HISTORY:]

    stats[user_id]["questions"] += 1
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        text = await get_ai_response(histories[user_id])
        histories[user_id].append({"role": "assistant", "content": text})

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Уточнить", callback_data="search")],
            [InlineKeyboardButton("🏠 Меню",     callback_data="back")],
        ])
        await update.message.reply_text(text, reply_markup=keyboard)

    except httpx.HTTPStatusError as e:
        await update.message.reply_text(f"⚠️ Ошибка {e.response.status_code}:\n{e.response.text[:500]}")
    except Exception as error:
        print(f"Ошибка reply: {error}")
        await update.message.reply_text("⚠️ Что-то пошло не так, попробуй позже.")


# ──────────────────────────────────────────
# ГОЛОС
# ──────────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limit_check(update.effective_user.id):
        await update.message.reply_text("⏳ Не торопись!")
        return

    msg = await update.message.reply_text("🎤 Обрабатываю голос...")

    try:
        voice_file = await update.message.voice.get_file()
        voice_path = f"/tmp/voice_{update.effective_user.id}.ogg"
        await voice_file.download_to_drive(voice_path)

        with open(voice_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        os.remove(voice_path)

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
                            {"text": "Транскрибируй это аудио на русском языке. Только текст, без пояснений."}
                        ]
                    }]
                }
            )
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]

        await msg.edit_text(f"📝 Ты сказал: *{text}*\n\nОтвечаю...", parse_mode="Markdown")
        update.message.text = text
        await reply(update, context)

    except Exception as error:
        print(f"Ошибка голоса: {error}")
        await msg.edit_text("⚠️ Не смог распознать голос, напиши текстом.")


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


# ──────────────────────────────────────────
# ФОТО
# ──────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📸 Анализирую фото...")

    try:
        photo      = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_path = f"/tmp/photo_{update.effective_user.id}.jpg"
        await photo_file.download_to_drive(photo_path)

        with open(photo_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        os.remove(photo_path)

        result = await analyze_image_gemini(image_b64)
        await msg.edit_text(f"📋 Анализ фото:\n\n{result}")

    except Exception as error:
        print(f"Ошибка фото: {error}")
        await msg.edit_text(f"⚠️ Ошибка: {error}")


# ──────────────────────────────────────────
# ЗАПУСК
# ──────────────────────────────────────────
def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help",  help_cmd))
    bot.add_handler(CallbackQueryHandler(button_handler))
    bot.add_handler(MessageHandler(filters.VOICE,        handle_voice))
    bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    bot.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    print("✅ Сократ запущен на Gemini!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
