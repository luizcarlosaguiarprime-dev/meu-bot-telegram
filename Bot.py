import os
import telebot

TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(func=lambda message: True)
def responder(message):
    bot.reply_to(message, "Olá! Seu bot está funcionando! 🤖")

bot.infinity_polling()
