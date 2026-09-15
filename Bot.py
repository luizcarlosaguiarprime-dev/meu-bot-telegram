import os
import telebot
from flask import Flask
from threading import Thread

TOKEN = os.environ["BOT_TOKEN"]

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot funcionando!"

def run():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

@bot.message_handler(func=lambda message: True)
def responder(message):
    bot.reply_to(message, "Olá! Seu bot está funcionando! 🤖")

Thread(target=run).start()

bot.infinity_polling()
