import requests
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TELEGRAM_TOKEN = "8401075719:AAEjXWcERcS9IEwRN9HKJQV8ivG7lwuEqUE"
GROQ_API_KEY = "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"

SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ, который изучил всё законодательство РФ.

Твой стиль:
- Отвечай КРАТКО — максимум 3-4 предложения
- Цитируй конкретные статьи (УК РФ, ГК РФ, ТК РФ, КоАП, Конституция)
- Без воды и лишних слов
- Всегда предупреждай что ты не замена юристу
- Только на русском

ВАЖНО: Ответ должен быть коротким и по делу."""

histories = {}


def get_ai_response(messages):
    """Общая функция для запроса к AI"""
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 300,
            "temperature": 0.7
        }
    )
    return response.json()["choices"][0]["message"]["content"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск по кодексу", callback_data="search")],
        [InlineKeyboardButton("📄 Анализ документа", callback_data="doc_help")],
        [InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🏛️ Привет! Я Сократ, но шарю в законах РФ\n\n"
        "Что умею:\n"
        "• Отвечаю на вопросы по закону\n"
        "• Ищу статьи в УК/ГК/ТК/КоАП\n"
        "• Анализирую документы (договоры, жалобы)\n"
        "• Понимаю голосовые сообщения 🎤\n\n"
        "Просто пиши или говори вопрос! ⚖️",
        reply_markup=reply_markup
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "clear":
        user_id = query.from_user.id
        histories[user_id] = []
        await query.edit_message_text("🗑️ История очищена!")
        
    elif query.data == "search":
        await query.edit_message_text(
            "🔍 Поиск по кодексам\n\n"
            "Напиши что ищешь, например:\n"
            "• УК РФ статья 228\n"
            "• ГК РФ возмещение ущерба\n"
            "• ТК РФ увольнение\n"
            "• КоАП превышение скорости"
        )
        
    elif query.data == "doc_help":
        await query.edit_message_text(
            "📄 Анализ документов\n\n"
            "Отправь мне документ (PDF/DOC/TXT/фото) и напиши что проверить:\n\n"
            "Примеры:\n"
            "• Проверь договор на подводные камни\n"
            "• Есть ли тут незаконные пункты?\n"
            "• Что не так в этой жалобе?"
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосовых сообщений"""
    await update.message.reply_text("🎤 Слушаю... (транскрибирую)")
    
    try:
        # Скачиваем голосовое
        voice_file = await update.message.voice.get_file()
        voice_path = f"/tmp/voice_{update.effective_user.id}.ogg"
        await voice_file.download_to_drive(voice_path)
        
        # Транскрибация через Groq Whisper API
        with open(voice_path, "rb") as f:
            transcribe_response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": f},
                data={"model": "whisper-large-v3"}
            )
        
        text = transcribe_response.json()["text"]
        await update.message.reply_text(f"📝 Ты сказал: {text}\n\nОтвечаю...")
        
        # Обрабатываем как обычный текст
        update.message.text = text
        await reply(update, context)
        
        os.remove(voice_path)
        
    except Exception as error:
        print(f"Ошибка голоса: {error}")
        await update.message.reply_text(f"⚠️ Не смог распознать: {error}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ документов"""
    await update.message.reply_text("📄 Анализирую документ...")
    
    try:
        doc = update.message.document
        doc_file = await doc.get_file()
        doc_path = f"/tmp/doc_{update.effective_user.id}_{doc.file_name}"
        await doc_file.download_to_drive(doc_path)
        
        # Читаем текст (упрощенно для txt)
        with open(doc_path, "r", encoding="utf-8") as f:
            doc_text = f.read()[:3000]  # первые 3000 символов
        
        # Анализируем
        analysis_prompt = f"""Проанализируй этот документ как юрист:

{doc_text}

Найди:
1. Подводные камни и риски
2. Незаконные или сомнительные пункты
3. Что можно улучшить

Ответ КРАТКО."""

        result = get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": analysis_prompt}
        ])
        
        await update.message.reply_text(f"📋 Анализ:\n\n{result}")
        os.remove(doc_path)
        
    except Exception as error:
        print(f"Ошибка документа: {error}")
        await update.message.reply_text(f"⚠️ Не смог прочитать: {error}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ фото документов"""
    await update.message.reply_text("📸 Распознаю текст на фото...")
    
    try:
        photo = update.message.photo[-1]  # самое большое фото
        photo_file = await photo.get_file()
        photo_path = f"/tmp/photo_{update.effective_user.id}.jpg"
        await photo_file.download_to_drive(photo_path)
        
        # Здесь нужен OCR (tesseract), упрощенно отвечаем
        await update.message.reply_text(
            "📸 Для анализа фото отправь документ в формате PDF или TXT.\n"
            "Или перепиши текст с фото вручную."
        )
        
        os.remove(photo_path)
        
    except Exception as error:
        print(f"Ошибка фото: {error}")
        await update.message.reply_text(f"⚠️ Ошибка: {error}")


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    question = update.message.text

    if user_id not in histories:
        histories[user_id] = []

    histories[user_id].append({"role": "user", "content": question})

    if len(histories[user_id]) > 10:
        histories[user_id] = histories[user_id][-10:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        text = get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            *histories[user_id]
        ])
        
        histories[user_id].append({"role": "assistant", "content": text})
        await update.message.reply_text(text)

    except Exception as error:
        print(f"Ошибка: {error}")
        await update.message.reply_text(f"⚠️ Что-то сломалось\n{error}")


def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(button_handler))
    bot.add_handler(MessageHandler(filters.VOICE, handle_voice))
    bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    bot.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    print("✅ Сократ запущен с фичами!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
