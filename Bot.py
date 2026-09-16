import os
import requests
import telebot
from flask import Flask
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["BOT_TOKEN"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot funcionando!"

def run():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "Olá! Seu bot está funcionando! 🤖\n\n"
        "Use /odds para consultar as odds de futebol."
    )

@bot.message_handler(commands=["odds"])
def odds(message):
    try:
        url = "https://api.the-odds-api.com/v4/sports/soccer/odds"

        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal"
        }

        resposta = requests.get(url, params=params, timeout=15)
        resposta.raise_for_status()

        jogos = resposta.json()

        if not jogos:
            bot.reply_to(
                message,
                "⚽ Não encontrei jogos disponíveis no momento."
            )
            return

        texto = "⚽ ODDS DE FUTEBOL\n\n"

        for jogo in jogos[:10]:

            home = jogo.get("home_team", "")
            away = jogo.get("away_team", "")

            # Data e horário da partida
            commence_time = jogo.get("commence_time")

            if commence_time:
                data_utc = datetime.fromisoformat(
                    commence_time.replace("Z", "+00:00")
                )

                horario_brasilia = data_utc.astimezone(
                    ZoneInfo("America/Sao_Paulo")
                )

                data_hora = horario_brasilia.strftime(
                    "%d/%m/%Y às %H:%M"
                )
            else:
                data_hora = "Horário indisponível"

            texto += f"🏟️ {home} x {away}\n"
            texto += f"📅 {data_hora} (Brasília)\n"

            bookmakers = jogo.get("bookmakers", [])

            if bookmakers:
                bookmaker = bookmakers[0]

                texto += f"🏦 {bookmaker.get('title', '')}\n"

                mercados = bookmaker.get("markets", [])

                if mercados:
                    outcomes = mercados[0].get("outcomes", [])

                    for outcome in outcomes:
                        nome = outcome.get("name", "")
                        odd = outcome.get("price", "")

                        texto += f"• {nome}: {odd}\n"

            texto += "\n"

        bot.reply_to(message, texto)

    except Exception as e:
        bot.reply_to(
            message,
            f"❌ Erro ao consultar as odds:\n{e}"
        )

@bot.message_handler(func=lambda message: True)
def responder(message):
    bot.reply_to(
        message,
        "Comando não reconhecido.\n\n"
        "Use /odds para consultar as odds."
    )

Thread(target=run).start()

bot.infinity_polling()
