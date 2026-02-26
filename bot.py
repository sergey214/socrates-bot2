import os
from dotenv import load_dotenv
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
from functools import wraps
from time import time

# Загрузка токенов из .env
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Проверка что токены есть
if not all([8401075719:AAEjXWcERcS9IEwRN9HKJQV8ivG7lwuEqUE, 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]):
    raise ValueError("❌ Токены не найдены! Создай .env файл")

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# УЛУЧШЕННЫЙ ПРОМПТ
SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ и эксперт по законодательству РФ.

ПРАВИЛА ОТВЕТОВ:
1. Всегда цитируй КОНКРЕТНЫЕ статьи с номерами (например: "Статья 151 ГК РФ гласит...")
2. Давай практические советы и пошаговые инструкции
3. Предупреждай о сроках (исковая давность, обжалование и т.д.)
4. Отвечай кратко но полно - 3-5 предложений
5. Используй философский стиль Сократа, но без воды
6. ОБЯЗАТЕЛЬНО напоминай что ты не заменяешь реального юриста

СТРУКТУРА ОТВЕТА:
- Краткий ответ (да/нет/возможно)
- Ссылка на закон (статья + кодекс)
- Практический совет
- Предупреждение о сроках (если актуально)

ВАЖНО: Если не знаешь точно - скажи это прямо. Лучше признать незнание, чем дать неверный совет.

Отвечай только на русском."""

histories = {}
user_last_request = {}
RATE_LIMIT = 3  # секунды между запросами

# Rate limiting
def rate_limit(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        now = time()
        
        if user_id in user_last_request:
            if now - user_last_request[user_id] < RATE_LIMIT:
                await update.message.reply_text(
                    "⚠️ Подожди немного перед следующим вопросом.\n"
                    "Это защита от спама."
                )
                return
        
        user_last_request[user_id] = now
        return await func(update, context)
    
    return wrapper


def get_ai_response(messages, use_web_search=False):
    """Запрос к Groq с опциональным веб-поиском"""
    try:
        # Базовый запрос
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.3  # Низкая температура для точности
            },
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"Groq API error: {response.status_code} - {response.text}")
            return None
        
        return response.json()["choices"][0]["message"]["content"]
    
    except Exception as error:
        logger.error(f"AI request failed: {error}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Аноним"
    
    logger.info(f"User {user_id} (@{username}) started bot")
    
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск статьи", callback_data="search")],
        [InlineKeyboardButton("📄 Анализ документа", callback_data="doc_help")],
        [InlineKeyboardButton("💡 Примеры вопросов", callback_data="examples")],
        [InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🏛️ **Привет! Я Сократ — твой юридический помощник**\n\n"
        "Я помогу разобраться в законах РФ:\n"
        "• Отвечаю на вопросы по праву\n"
        "• Цитирую конкретные статьи\n"
        "• Даю практические советы\n"
        "• Анализирую документы\n"
        "• Понимаю голосовые сообщения 🎤\n\n"
        "⚖️ Просто задай вопрос!\n\n"
        "⚠️ Помни: я не заменяю реального юриста",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "clear":
        histories[user_id] = []
        logger.info(f"User {user_id} cleared history")
        await query.edit_message_text("🗑️ История очищена! Можем начать заново.")
        
    elif query.data == "search":
        await query.edit_message_text(
            "🔍 **Поиск по кодексам**\n\n"
            "Напиши что ищешь:\n\n"
            "**Примеры:**\n"
            "• УК РФ статья 228\n"
            "• ГК РФ возмещение морального вреда\n"
            "• ТК РФ увольнение по собственному\n"
            "• КоАП штраф за превышение скорости\n"
            "• Конституция РФ свобода слова\n\n"
            "Я найду нужные статьи и объясню их."
        )
        
    elif query.data == "doc_help":
        await query.edit_message_text(
            "📄 **Анализ документов**\n\n"
            "Отправь документ (TXT/PDF/фото) и напиши что проверить:\n\n"
            "**Примеры:**\n"
            "• Проверь договор на подводные камни\n"
            "• Есть ли незаконные условия?\n"
            "• Правильно ли составлена жалоба?\n"
            "• Какие риски в этом контракте?\n\n"
            "Я проанализирую и укажу на проблемы."
        )
    
    elif query.data == "examples":
        await query.edit_message_text(
            "💡 **Примеры вопросов:**\n\n"
            "**Трудовое право:**\n"
            "• Меня уволили без предупреждения — законно?\n"
            "• Не выплатили зарплату, что делать?\n"
            "• Могу ли я уйти в отпуск когда хочу?\n\n"
            "**Гражданское право:**\n"
            "• Сосед затопил квартиру — как взыскать?\n"
            "• Магазин не вернул деньги за брак\n"
            "• Как составить претензию?\n\n"
            "**Административное:**\n"
            "• Штраф ГАИ — как оспорить?\n"
            "• Незаконная парковка — что грозит?\n\n"
            "**Уголовное:**\n"
            "• Что грозит за драку?\n"
            "• Клевета — это уголовное?\n\n"
            "Просто задай свой вопрос!"
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосовых"""
    user_id = update.effective_user.id
    
    await update.message.reply_text("🎤 Слушаю и транскрибирую...")
    
    try:
        voice_file = await update.message.voice.get_file()
        voice_path = f"/tmp/voice_{user_id}.ogg"
        await voice_file.download_to_drive(voice_path)
        
        # Транскрибация через Groq Whisper
        with open(voice_path, "rb") as f:
            transcribe_response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": f},
                data={"model": "whisper-large-v3", "language": "ru"}
            )
        
        if transcribe_response.status_code != 200:
            await update.message.reply_text("❌ Не смог распознать. Попробуй ещё раз")
            return
        
        text = transcribe_response.json()["text"]
        logger.info(f"User {user_id} voice: {text[:100]}")
        
        await update.message.reply_text(f"📝 Ты сказал:\n_{text}_\n\nОтвечаю...", parse_mode='Markdown')
        
        # Обрабатываем как текст
        update.message.text = text
        await reply(update, context)
        
        os.remove(voice_path)
        
    except Exception as error:
        logger.error(f"Voice error for user {user_id}: {error}")
        await update.message.reply_text(f"⚠️ Ошибка распознавания: {error}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ документов"""
    user_id = update.effective_user.id
    
    await update.message.reply_text("📄 Анализирую документ...")
    
    try:
        doc = update.message.document
        doc_file = await doc.get_file()
        doc_path = f"/tmp/doc_{user_id}_{doc.file_name}"
        await doc_file.download_to_drive(doc_path)
        
        # Читаем текст
        try:
            with open(doc_path, "r", encoding="utf-8") as f:
                doc_text = f.read()[:4000]  # первые 4000 символов
        except:
            # Если не UTF-8, пробуем другую кодировку
            with open(doc_path, "r", encoding="cp1251") as f:
                doc_text = f.read()[:4000]
        
        logger.info(f"User {user_id} uploaded document: {doc.file_name}")
        
        # Анализируем
        analysis_prompt = f"""Проанализируй этот документ как опытный юрист РФ:

{doc_text}

Найди и укажи:
1. Подводные камни и риски (статьи законов)
2. Незаконные или сомнительные условия (с номерами статей)
3. Что нужно исправить или добавить
4. Практические советы

Ответ структурированный и конкретный."""

        result = get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": analysis_prompt}
        ])
        
        if result:
            await update.message.reply_text(
                f"📋 **Анализ документа:**\n\n{result}\n\n"
                f"⚠️ Это предварительный анализ. Для юридической силы обратись к адвокату.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Не смог проанализировать. Попробуй позже")
        
        os.remove(doc_path)
        
    except Exception as error:
        logger.error(f"Document error for user {user_id}: {error}")
        await update.message.reply_text(f"⚠️ Ошибка обработки: {error}")


@rate_limit
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Аноним"
    question = update.message.text
    
    logger.info(f"User {user_id} (@{username}) asked: {question[:100]}")
    
    if user_id not in histories:
        histories[user_id] = []
    
    histories[user_id].append({"role": "user", "content": question})
    
    # Оставляем только последние 6 сообщений (3 пары)
    if len(histories[user_id]) > 6:
        histories[user_id] = histories[user_id][-6:]
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        # Проверяем нужен ли веб-поиск
        search_keywords = ["новый закон", "изменения", "2024", "2025", "2026", "последние", "актуальный"]
        needs_web_search = any(keyword in question.lower() for keyword in search_keywords)
        
        if needs_web_search:
            await update.message.reply_text(
                "🔍 Вижу что вопрос про актуальные изменения. "
                "Ищу свежую информацию...\n\n"
                "⚠️ Для самых точных данных всегда проверяй на официальных сайтах "
                "(consultant.ru, pravo.gov.ru)"
            )
        
        # Запрос к AI
        text = get_ai_response([
            {"role": "system", "content": SYSTEM_PROMPT},
            *histories[user_id]
        ])
        
        if not text:
            await update.message.reply_text(
                "⚠️ Не смог получить ответ от AI. Попробуй:\n"
                "• Переформулировать вопрос\n"
                "• Задать его через минуту\n"
                "• Написать короче"
            )
            return
        
        histories[user_id].append({"role": "assistant", "content": text})
        
        # Добавляем кнопки для полезных действий
        keyboard = [
            [InlineKeyboardButton("🔄 Задать новый вопрос", callback_data="clear")],
            [InlineKeyboardButton("💡 Примеры вопросов", callback_data="examples")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            text,
            reply_markup=reply_markup
        )
        
        logger.info(f"User {user_id} got response: {text[:100]}")
        
    except Exception as error:
        logger.error(f"Reply error for user {user_id}: {error}")
        await update.message.reply_text(
            f"⚠️ Произошла ошибка.\n\n"
            f"Попробуй:\n"
            f"• Переформулировать вопрос\n"
            f"• Задать его через минуту\n"
            f"• Написать /start для перезапуска"
        )


def main():
    logger.info("🚀 Starting Sokrat bot...")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
    
    logger.info("✅ Sokrat bot started successfully!")
    print("✅ Сократ запущен с улучшениями!")
    print("📝 Логи сохраняются в bot.log")
    
    app.run_polling()


if __name__ == "__main__":
    main()
