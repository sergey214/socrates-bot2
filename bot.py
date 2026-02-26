import httpx
import os
import asyncio
import base64
import time
from collections import defaultdict
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ──────────────────────────────────────────
# КОНФИГ — читаем из .env файла
# ──────────────────────────────────────────
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Нет токенов! Создай .env файл (см. .env.example)")

# ──────────────────────────────────────────
# СИСТЕМНЫЙ ПРОМПТ
# ──────────────────────────────────────────
SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ, который изучил всё законодательство РФ.

Твой стиль:
- Отвечай КРАТКО — максимум 3-4 предложения
- Цитируй конкретные статьи (УК РФ, ГК РФ, ТК РФ, КоАП, Конституция)
- Без воды и лишних слов
- Всегда предупреждай что ты не замена юристу
- Только на русском

ВАЖНО: Ответ должен быть коротким и по делу."""

# ──────────────────────────────────────────
# ХРАНИЛИЩЕ (история + статистика)
# ──────────────────────────────────────────
histories: dict[int, list]  = defaultdict(list)
stats:     dict[int, dict]  = defaultdict(lambda: {"questions": 0, "joined": time.strftime("%d.%m.%Y")})
user_last_request: dict[int, float] = defaultdict(float)

RATE_LIMIT_SECONDS = 3   # пауза между запросами
MAX_HISTORY        = 10  # сколько сообщений помним

# ──────────────────────────────────────────
# AI — асинхронный запрос (не блокирует бот)
# ──────────────────────────────────────────
async def get_ai_response(messages: list[dict], max_tokens: int = 350) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7
            }
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def transcribe_audio(file_path: str) -> str:
    """Whisper через Groq — распознаём голос"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.ogg", f, "audio/ogg")},
                data={"model": "whisper-large-v3", "language": "ru"}
            )
        response.raise_for_status()
        return response.json()["text"]


# ──────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────
def rate_limit_check(user_id: int) -> bool:
    """True — можно отвечать, False — слишком быстро"""
    now = time.time()
    if now - user_last_request[user_id] < RATE_LIMIT_SECONDS:
        return False
    user_last_request[user_id] = now
    return True


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск по кодексу",  callback_data="search")],
        [InlineKeyboardButton("📄 Анализ документа",  callback_data="doc_help")],
        [InlineKeyboardButton("📊 Моя статистика",    callback_data="my_stats")],
        [InlineKeyboardButton("🗑️ Очистить историю",  callback_data="clear")],
    ])


def read_document_text(path: str, filename: str) -> str:
    """Читаем TXT и PDF"""
    if filename.lower().endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return " ".join(p.extract_text() or "" for p in reader.pages)[:4000]
        except ImportError:
            return "[PDF: установи pypdf: pip install pypdf]"
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()[:4000]


# ──────────────────────────────────────────
# КОМАНДЫ
# ──────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    stats[user.id]  # создаём запись если нет
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
        "• Что такое самозащита по ГК РФ?\n"
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
            "• ТК РФ увольнение\n"
            "• КоАП превышение скорости",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]])
        )

    elif query.data == "doc_help":
        await query.edit_message_text(
            "📄 Анализ документов\n\n"
            "Отправь PDF или TXT файл, я:\n"
            "• Найду подводные камни\n"
            "• Укажу на незаконные пункты\n"
            "• Дам рекомендации\n\n"
            "Или отправь фото — распознаю текст 📸",
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
# ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТА
# ──────────────────────────────────────────
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    question = update.message.text

    # Rate limit
    if not rate_limit_check(user_id):
        await update.message.reply_text("⏳ Не торопись, подожди пару секунд!")
        return

    # Обновляем историю
    histories[user_id].append({"role": "user", "content": question})
    if len(histories[user_id]) > MAX_HISTORY:
        histories[user_id] = histories[user_id][-MAX_HISTORY:]

    stats[user_id]["questions"] += 1
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        text = await get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            *histories[user_id]
        ])
        histories[user_id].append({"role": "assistant", "content": text})

        # Кнопки после ответа
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Уточнить вопрос", callback_data="search")],
            [InlineKeyboardButton("🏠 Меню",            callback_data="back")],
        ])
        await update.message.reply_text(text, reply_markup=keyboard)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            await update.message.reply_text("⏳ API перегружен, подожди минуту и спроси снова.")
        else:
            await update.message.reply_text(f"⚠️ Ошибка API: {e.response.status_code}")
    except Exception as error:
        print(f"Ошибка reply: {error}")
        await update.message.reply_text("⚠️ Что-то пошло не так, попробуй позже.")


# ──────────────────────────────────────────
# ГОЛОСОВЫЕ СООБЩЕНИЯ
# ──────────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not rate_limit_check(update.effective_user.id):
        await update.message.reply_text("⏳ Не торопись!")
        return

    msg = await update.message.reply_text("🎤 Транскрибирую...")

    try:
        voice_file = await update.message.voice.get_file()
        voice_path = f"/tmp/voice_{update.effective_user.id}.ogg"
        await voice_file.download_to_drive(voice_path)

        text = await transcribe_audio(voice_path)
        os.remove(voice_path)

        await msg.edit_text(f"📝 Ты сказал: *{text}*\n\nОтвечаю...", parse_mode="Markdown")

        # Подменяем текст и вызываем основной обработчик
        update.message.text = text
        await reply(update, context)

    except Exception as error:
        print(f"Ошибка голоса: {error}")
        await msg.edit_text(f"⚠️ Не смог распознать голос: {error}")


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

        analysis_prompt = (
            f"Проанализируй этот документ как юрист:\n\n{doc_text}\n\n"
            "Найди: 1) Риски и подводные камни 2) Незаконные пункты 3) Что улучшить. Ответ КРАТКО."
        )

        result = await get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": analysis_prompt}
        ], max_tokens=500)

        await msg.edit_text(f"📋 Анализ документа:\n\n{result}")

    except Exception as error:
        print(f"Ошибка документа: {error}")
        await msg.edit_text(f"⚠️ Ошибка при чтении: {error}")


# ──────────────────────────────────────────
# ФОТО — Vision через Groq
# ──────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📸 Распознаю текст на фото...")

    try:
        photo      = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_path = f"/tmp/photo_{update.effective_user.id}.jpg"
        await photo_file.download_to_drive(photo_path)

        with open(photo_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        os.remove(photo_path)

        # Groq Vision
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Ты юрист. Извлеки весь текст с фото и проанализируй его: "
                                    "найди риски, незаконные пункты, дай рекомендации. "
                                    "Если это не документ — скажи об этом. Отвечай на русском."
                                )
                            }
                        ]
                    }],
                    "max_tokens": 500
                }
            )
            response.raise_for_status()
            result = response.json()["choices"][0]["message"]["content"]

        await msg.edit_text(f"📋 Анализ фото:\n\n{result}")

    except httpx.HTTPStatusError as e:
        # Если Vision модель недоступна — сообщаем пользователю
        await msg.edit_text(
            "📸 Vision временно недоступен.\n\n"
            "Отправь документ в формате PDF или TXT — разберу точнее!"
        )
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

    print("✅ Сократ запущен!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
