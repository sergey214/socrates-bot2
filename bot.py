import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = "8401075719:AAEjXWcERcS9IEwRN9HKJQV8ivG7lwuEqUE"
OPENROUTER_API_KEY = "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"  # Получи на openrouter.ai

SYSTEM_PROMPT = """Ты — Сократ, древнегреческий философ, который изучил всё законодательство РФ.

Твой стиль:
- Говоришь мудро но по делу, иногда задаёшь встречные вопросы
- Цитируешь конкретные статьи (УК РФ, ГК РФ, ТК РФ, КоАП, Конституция и т.д.)
- Иногда говоришь типа "Я знаю лишь то, что ничего не знаю... но статья 151 ГК РФ говорит следующее"
- Всегда предупреждаешь что ты не замена настоящему юристу
- Отвечаешь только на русском"""

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
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",  # Бесплатная модель
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *histories[user_id]
                ]
            }
        )

        text = response.json()["choices"][0]["message"]["content"]
        histories[user_id].append({"role": "assistant", "content": text})
        await update.message.reply_text(text)

    except Exception as error:
        print(f"Ошибка: {error}")
        await update.message.reply_text(f"⚠️ Что-то сломалось, попробуй ещё раз\n{error}")


def main():
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("clear", clear))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    print("✅ Сократ запущен!")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
