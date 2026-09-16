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

def calcular_consenso(bookmakers):
    """
    Calcula a probabilidade de mercado usando
    as odds disponíveis e removendo a margem.
    """
    probabilidades = {}

    for bookmaker in bookmakers:
        mercados = bookmaker.get("markets", [])

        if not mercados:
            continue

        mercado = mercados[0]

        if mercado.get("key") != "h2h":
            continue

        outcomes = mercado.get("outcomes", [])

        if not outcomes:
            continue

        probs = {}

        for outcome in outcomes:
            nome = outcome.get("name")
            odd = outcome.get("price")

            if nome and odd and odd > 1:
                probs[nome] = 1 / odd

        total = sum(probs.values())

        if total > 0:
            for nome, prob in probs.items():
                probabilidades.setdefault(nome, []).append(
                    prob / total
                )

    consenso = {}

    for nome, valores in probabilidades.items():
        consenso[nome] = sum(valores) / len(valores)

    return consenso


def calcular_ev(probabilidade, odd):
    return (probabilidade * odd - 1) * 100


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "Olá! Seu bot está funcionando! 🤖\n\n"
        "Use /odds para consultar as odds e oportunidades de mercado."
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
                "⚽ Não encontrei jogos disponíveis."
            )
            return

        encontrou_ev = False

        texto = "🔥 OPORTUNIDADES DE MERCADO\n\n"

        for jogo in jogos:

            bookmakers = jogo.get("bookmakers", [])

            if len(bookmakers) < 2:
                continue

            consenso = calcular_consenso(bookmakers)

            if not consenso:
                continue

            home = jogo.get("home_team", "")
            away = jogo.get("away_team", "")

            commence_time = jogo.get("commence_time")

            if commence_time:
                data_utc = datetime.fromisoformat(
                    commence_time.replace("Z", "+00:00")
                )

                horario = data_utc.astimezone(
                    ZoneInfo("America/Sao_Paulo")
                )

                data_hora = horario.strftime(
                    "%d/%m/%Y às %H:%M"
                )
            else:
                data_hora = "Horário indisponível"

            melhores = []

            for bookmaker in bookmakers:

                mercados = bookmaker.get("markets", [])

                if not mercados:
                    continue

                mercado = mercados[0]

                if mercado.get("key") != "h2h":
                    continue

                for outcome in mercado.get("outcomes", []):

                    nome = outcome.get("name")
                    odd = outcome.get("price")

                    if nome not in consenso:
                        continue

                    if not odd or odd <= 1:
                        continue

                    prob = consenso[nome]

                    ev = calcular_ev(prob, odd)

                    odd_justa = 1 / prob

                    melhores.append({
                        "nome": nome,
                        "odd": odd,
                        "prob": prob,
                        "odd_justa": odd_justa,
                        "ev": ev,
                        "casa": bookmaker.get("title", "")
                    })

            # Mostra somente oportunidades com EV relativo >= 3%
            oportunidades = [
                x for x in melhores
                if x["ev"] >= 3
            ]

            if not oportunidades:
                continue

            oportunidades.sort(
                key=lambda x: x["ev"],
                reverse=True
            )

            encontrou_ev = True

            texto += f"⚽ {home} x {away}\n"
            texto += f"📅 {data_hora} (Brasília)\n\n"

            for op in oportunidades[:3]:

                texto += f"🎯 {op['nome']}\n"
                texto += f"🏦 {op['casa']}\n"
                texto += f"💰 Odd: {op['odd']:.2f}\n"
                texto += f"📊 Prob. mercado: {op['prob'] * 100:.1f}%\n"
                texto += f"📐 Odd justa: {op['odd_justa']:.2f}\n"
                texto += f"📈 EV mercado: +{op['ev']:.2f}%\n\n"

            texto += "━━━━━━━━━━━━━━\n\n"

        if not encontrou_ev:
            texto = (
                "🔎 Nenhuma oportunidade com EV de mercado "
                "≥ 3% foi encontrada no momento."
            )

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
        "Use /odds para consultar as oportunidades."
    )


Thread(target=run).start()

bot.infinity_polling()
