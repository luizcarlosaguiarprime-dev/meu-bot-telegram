import os
import requests
import telebot
from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# =========================
# CONFIGURAÇÕES
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0
BANCA = 1000.00
KELLY_FRACAO = 0.25

FUSO = ZoneInfo("America/Sao_Paulo")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Futebol
ESPORTES = {
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Serie A": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga",
    "Ligue 1": "soccer_france_ligue_one",
    "Brasileirão": "soccer_brazil_campeonato",
    "Champions League": "soccer_uefa_champs_league",
    "Europa League": "soccer_uefa_europa_league",
    "Conference League": "soccer_uefa_europa_conference_league",
    "Libertadores": "soccer_conmebol_libertadores",
    "Sul-Americana": "soccer_conmebol_sudamericana",
}


# =========================
# FLASK
# =========================

@app.route("/")
def home():
    return "Bot de apostas online."


def iniciar_servidor():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# =========================
# BUSCAR ODDS
# =========================

def buscar_odds(nome_liga, esporte):

    url = f"https://api.the-odds-api.com/v4/sports/{esporte}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,btts",
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(url, params=params, timeout=20)

        print(f"[API] {nome_liga} -> HTTP {resposta.status_code}")

        if resposta.status_code != 200:
            print(f"[API] Erro: {resposta.text[:500]}")
            return []

        dados = resposta.json()

        print(f"[API] {nome_liga} -> {len(dados)} jogos encontrados")

        return dados

    except Exception as erro:

        print(f"[API] Erro em {nome_liga}: {erro}")

        return []


# =========================
# PROBABILIDADE
# =========================

def calcular_probabilidade(odd):

    if odd <= 1:
        return 0

    return 1 / odd


# =========================
# ANÁLISE
# =========================

def analisar():

    agora = datetime.now(FUSO)

    amanha = agora.date() + timedelta(days=1)

    print("")
    print("====================================")
    print("INICIANDO ANÁLISE")
    print(f"Data dos jogos: {amanha}")
    print("====================================")

    oportunidades = []

    for nome_liga, esporte in ESPORTES.items():

        eventos = buscar_odds(nome_liga, esporte)

        for evento in eventos:

            try:

                horario = datetime.fromisoformat(
                    evento["commence_time"].replace("Z", "+00:00")
                )

                horario = horario.astimezone(FUSO)

                # Somente jogos de amanhã
                if horario.date() != amanha:
                    continue

                times = f'{evento["home_team"]} x {evento["away_team"]}'

                bookmakers = evento.get("bookmakers", [])

                if not bookmakers:
                    continue

                # =========================
                # H2H
                # =========================

                mercado_h2h = {}

                for bookmaker in bookmakers:

                    for market in bookmaker.get("markets", []):

                        if market["key"] != "h2h":
                            continue

                        for outcome in market.get("outcomes", []):

                            nome = outcome["name"]
                            odd = outcome.get("price")

                            if not odd:
                                continue

                            mercado_h2h.setdefault(nome, []).append({
                                "odd": odd,
                                "casa": bookmaker["title"]
                            })

                # =========================
                # TOTALS
                # =========================

                mercado_totals = {}

                for bookmaker in bookmakers:

                    for market in bookmaker.get("markets", []):

                        if market["key"] != "totals":
                            continue

                        for outcome in market.get("outcomes", []):

                            nome = outcome["name"]
                            ponto = outcome.get("point")
                            odd = outcome.get("price")

                            if not odd or ponto is None:
                                continue

                            chave = f"{nome} {ponto}"

                            mercado_totals.setdefault(chave, []).append({
                                "odd": odd,
                                "casa": bookmaker["title"]
                            })

                # =========================
                # BTTS
                # =========================

                mercado_btts = {}

                for bookmaker in bookmakers:

                    for market in bookmaker.get("markets", []):

                        if market["key"] != "btts":
                            continue

                        for outcome in market.get("outcomes", []):

                            nome = outcome["name"]
                            odd = outcome.get("price")

                            if not odd:
                                continue

                            mercado_btts.setdefault(nome, []).append({
                                "odd": odd,
                                "casa": bookmaker["title"]
                            })

                mercados = [
                    ("Resultado", mercado_h2h),
                    ("Total de gols", mercado_totals),
                    ("Ambas marcam", mercado_btts)
                ]

                for nome_mercado, mercado in mercados:

                    for selecao, odds in mercado.items():

                        if len(odds) < 2:
                            continue

                        # Usa a mediana das probabilidades
                        probabilidades = [
                            calcular_probabilidade(x["odd"])
                            for x in odds
                        ]

                        probabilidades.sort()

                        meio = len(probabilidades) // 2

                        if len(probabilidades) % 2:
                            prob = probabilidades[meio]
                        else:
                            prob = (
                                probabilidades[meio - 1]
                                + probabilidades[meio]
                            ) / 2

                        odd_melhor = max(odds, key=lambda x: x["odd"])

                        odd = odd_melhor["odd"]

                        prob = min(prob, 0.99)

                        odd_justa = 1 / prob

                        ev = ((prob * odd) - 1) * 100

                        # Kelly fracionado
                        b = odd - 1
                        q = 1 - prob

                        kelly = ((b * prob) - q) / b

                        stake = max(
                            0,
                            BANCA * kelly * KELLY_FRACAO
                        )

                        if ev < EV_MINIMO:
                            continue

                        oportunidades.append({
                            "liga": nome_liga,
                            "jogo": times,
                            "horario": horario,
                            "mercado": nome_mercado,
                            "selecao": selecao,
                            "casa": odd_melhor["casa"],
                            "odd": odd,
                            "prob": prob * 100,
                            "odd_justa": odd_justa,
                            "ev": ev,
                            "stake": stake
                        })

            except Exception as erro:

                print(f"[ANÁLISE] Erro no jogo: {erro}")

    oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    print("")
    print(f"OPORTUNIDADES ENCONTRADAS: {len(oportunidades)}")
    print("")

    return oportunidades


# =========================
# ENVIAR ANÁLISE
# =========================

def enviar_analise(chat_id):

    try:

        oportunidades = analisar()

        if not oportunidades:

            bot.send_message(
                chat_id,
                "🔎 Nenhuma oportunidade EV+ encontrada para amanhã dentro dos filtros definidos."
            )

            return

        bot.send_message(
            chat_id,
            f"✅ Encontrei {len(oportunidades)} oportunidade(s) EV+ para amanhã."
        )

        for op in oportunidades[:20]:

            mensagem = (
                f"⚽ {op['jogo']}\n"
                f"🏆 {op['liga']}\n"
                f"🕐 {op['horario'].strftime('%d/%m %H:%M')}\n\n"

                f"📊 Mercado: {op['mercado']}\n"
                f"🎯 Seleção: {op['selecao']}\n"
                f"🏦 Casa: {op['casa']}\n"
                f"💰 Odd: {op['odd']:.2f}\n\n"

                f"📈 Probabilidade estimada: {op['prob']:.1f}%\n"
                f"📐 Odd justa: {op['odd_justa']:.2f}\n"
                f"💎 EV: +{op['ev']:.2f}%\n"
                f"💵 Stake Kelly 25%: R$ {op['stake']:.2f}"
            )

            bot.send_message(chat_id, mensagem)

    except Exception as erro:

        print(f"[ENVIO] Erro: {erro}")

        bot.send_message(
            chat_id,
            "❌ Ocorreu um erro durante a análise. Veja os logs do Render."
        )


# =========================
# TELEGRAM
# =========================

@bot.message_handler(commands=["start"])
def start(message):

    bot.send_message(
        message.chat.id,
        "🤖 Bot de análise de apostas conectado!\n\n"
        "Use /odds para analisar as oportunidades EV+ dos jogos de amanhã."
    )


@bot.message_handler(commands=["odds"])
def odds(message):

    bot.send_message(
        message.chat.id,
        "🔎 Analisando as odds disponíveis...\n"
        "Isso pode levar alguns segundos."
    )

    Thread(
        target=enviar_analise,
        args=(message.chat.id,)
    ).start()


@bot.message_handler(func=lambda message: True)
def outras_mensagens(message):

    bot.send_message(
        message.chat.id,
        "Use /odds para analisar as oportunidades ou /start para iniciar."
    )


# =========================
# INICIAR
# =========================

if __name__ == "__main__":

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    print("================================")
    print("BOT INICIADO")
    print("================================")

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )
