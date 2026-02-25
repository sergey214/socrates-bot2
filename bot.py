import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛️ Привет! Я Сократ, но шарю в законах РФ\n\n"
        "Спрашивай про что угодно:\n"
        "• Уволили — законно или нет?\n"
        "• Сосед затопил — что делать?\n"
        "• Купил брак в магазине — как вернуть?\n"
        "• Штраф выписали — можно оспорить?\n\n"
        "Просто пиши вопрос! ⚖️\n\n"
        "/clear — начать разговор заново"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    histories[user_id] = []
    await update.message.reply_text("🗑️ Всё, начинаем с чистого листа!")


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
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *histories[user_id]
                ],
                "max_tokens": 300,
                "temperature": 0.7
            }
        )

        text = response.json()["choices"][0]["message"]["content"]
        histories[user_id].append({"role": "assistant", "content": text})
        await update.message.reply_text(text)

    except Exception as error:
        print(f"Ошибка: {error}")
        await update.message.reply_text(f"⚠️ Что-то сломалось\n{error}")


def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("clear", clear))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    print("✅ Сократ запущен!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
