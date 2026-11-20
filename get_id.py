import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

load_dotenv()
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user ID is {uid}")
    print("Message from user ID:", uid)


app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.ALL, handle))
print("Waiting for a message. Press Ctrl+C to stop.")
app.run_polling()