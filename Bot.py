import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0
MINIMO_CASAS = 3

KELLY_FRACAO = 0.25
BANCA = 1000.00

HORA_ANALISE = 9
MINUTO_ANALISE = 0

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

CHAT_ID = None
ULTIMO_ENVIO = None


# ============================================================
# COMPETIÇÕES
# ============================================================

ESPORTES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_brazil_campeonato",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_conmebol_libertadores",
    "soccer_conmebol_sudamericana",
]

NOMES_LIGAS = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_brazil_campeonato": "Brasileirão",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_conmebol_libertadores": "Libertadores",
    "soccer_conmebol_sudamericana": "Sul-Americana",
}

MERCADOS = [
    "h2h",
    "totals",
    "btts"
]


# ============================================================
# SERVIDOR
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():

    porta = int(os.getenv("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=porta
    )


# ============================================================
# HORÁRIO
# ============================================================

def agora_brasilia():

    return datetime.now(FUSO_BRASILIA)


def converter_horario(utc_time):

    try:

        dt = datetime.fromisoformat(
            utc_time.replace("Z", "+00:00")
        )

        return dt.astimezone(FUSO_BRASILIA)

    except Exception:

        return None


def formatar_data_hora(utc_time):

    dt = converter_horario(utc_time)

    if not dt:

        return "Horário não informado"

    return dt.strftime("%d/%m/%Y às %H:%M")


# ============================================================
# PERÍODO ANALISADO
# ============================================================

def periodo_analisado():

    agora = agora_brasilia()

    inicio = (
        agora.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
        + timedelta(days=1)
    )

    fim = inicio + timedelta(days=1)

    return inicio, fim


# ============================================================
# NOMES DOS MERCADOS
# ============================================================

def nome_mercado(mercado):

    if mercado == "h2h":
        return "Vencedor da partida (1X2)"

    if mercado == "totals":
        return "Mais/Menos gols"

    if mercado == "btts":
        return "Ambas Marcam"

    return mercado


# ============================================================
# BUSCAR ODDS
# ============================================================

def buscar_odds(esporte, mercado):

    url = (
        "https://api.the-odds-api.com/v4/"
        f"sports/{esporte}/odds"
    )

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": mercado,
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(
            url,
            params=params,
            timeout=30
        )

        if resposta.status_code == 401:

            print("❌ Chave da The Odds API inválida.")

            return []

        if resposta.status_code == 422:

            print(
                f"⚠️ {esporte}/{mercado}: "
                "mercado indisponível."
            )

            return []

        resposta.raise_for_status()

        dados = resposta.json()

        print(
            f"✅ {NOMES_LIGAS.get(esporte, esporte)} "
            f"- {mercado}: {len(dados)} jogos"
        )

        return dados

    except Exception as e:

        print(f"❌ Erro buscando odds: {e}")

        return []


# ============================================================
# BUSCAR JOGOS
# ============================================================

def buscar_jogos_do_dia():

    jogos = {}

    inicio, fim = periodo_analisado()

    print("📅 Período analisado:")

    print(
        inicio.strftime("%d/%m/%Y %H:%M")
        + " até "
        + fim.strftime("%d/%m/%Y %H:%M")
        + " Brasília"
    )

    for esporte in ESPORTES:

        liga = NOMES_LIGAS.get(
            esporte,
            esporte
        )

        for mercado in MERCADOS:

            dados = buscar_odds(
                esporte,
                mercado
            )

            for jogo in dados:

                horario = converter_horario(
                    jogo.get("commence_time")
                )

                if not horario:
                    continue

                if not (inicio <= horario < fim):
                    continue

                jogo_id = jogo.get("id")

                if not jogo_id:
                    continue

                if jogo_id not in jogos:

                    jogos[jogo_id] = {

                        "id": jogo_id,

                        "league": liga,

                        "sport_key": esporte,

                        "home_team":
                            jogo.get(
                                "home_team",
                                "?"
                            ),

                        "away_team":
                            jogo.get(
                                "away_team",
                                "?"
                            ),

                        "commence_time":
                            jogo.get(
                                "commence_time"
                            ),

                        "markets": {}

                    }

                for bookmaker in jogo.get(
                    "bookmakers",
                    []
                ):

                    for market in bookmaker.get(
                        "markets",
                        []
                    ):

                        chave = market.get("key")

                        if chave not in MERCADOS:
                            continue

                        if chave not in jogos[
                            jogo_id
                        ]["markets"]:

                            jogos[
                                jogo_id
                            ]["markets"][chave] = []

                        jogos[
                            jogo_id
                        ]["markets"][chave].append({

                            "bookmaker":
                                bookmaker.get(
                                    "title",
                                    "Casa"
                                ),

                            "outcomes":
                                market.get(
                                    "outcomes",
                                    []
                                )

                        })

    return list(jogos.values())


# ============================================================
# PROBABILIDADES
# ============================================================

def probabilidades_sem_margem(outcomes):

    probabilidades = {}

    inversas = []

    for outcome in outcomes:

        odd = outcome.get("price")

        nome = outcome.get("name")

        if not nome:
            continue

        if not odd or odd <= 1:
            continue

        inversas.append(
            (
                nome,
                1 / odd
            )
        )

    soma = sum(
        valor
        for _, valor in inversas
    )

    if soma <= 0:
        return {}

    for nome, valor in inversas:

        probabilidades[nome] = (
            valor / soma
        )

    return probabilidades


# ============================================================
# CONSENSO
# ============================================================

def calcular_consenso(casas):

    dados = {}

    for casa in casas:

        probabilidades = (
            probabilidades_sem_margem(
                casa["outcomes"]
            )
        )

        if not probabilidades:
            continue

        for nome, prob in probabilidades.items():

            if nome not in dados:
                dados[nome] = []

            dados[nome].append(prob)

    consenso = {}

    for nome, valores in dados.items():

        if len(valores) < MINIMO_CASAS:
            continue

        media = (
            sum(valores)
            / len(valores)
        )

        if media <= 0 or media >= 1:
            continue

        consenso[nome] = media

    return consenso


# ============================================================
# MELHOR ODD
# ============================================================

def melhor_odd(casas, selecao):

    melhor = None

    for casa in casas:

        for outcome in casa["outcomes"]:

            if outcome.get("name") != selecao:
                continue

            odd = outcome.get("price")

            if not odd or odd <= 1:
                continue

            if (
                melhor is None
                or odd > melhor["odd"]
            ):

                melhor = {

                    "casa":
                        casa["bookmaker"],

                    "odd":
                        odd

                }

    return melhor


# ============================================================
# KELLY
# ============================================================

def calcular_kelly(probabilidade, odd):

    if odd <= 1:
        return 0

    b = odd - 1

    q = 1 - probabilidade

    kelly = (
        (b * probabilidade - q)
        / b
    )

    if kelly <= 0:
        return 0

    return kelly * KELLY_FRACAO


def calcular_stake(probabilidade, odd):

    kelly = calcular_kelly(
        probabilidade,
        odd
    )

    stake = BANCA * kelly

    return stake, kelly


# ============================================================
# ANALISAR JOGO
# ============================================================

def analisar_jogo(jogo):

    oportunidades = []

    for mercado, casas in (
        jogo["markets"].items()
    ):

        if len(casas) < MINIMO_CASAS:
            continue

        consenso = calcular_consenso(casas)

        if not consenso:
            continue

        for selecao, probabilidade in consenso.items():

            melhor = melhor_odd(
                casas,
                selecao
            )

            if not melhor:
                continue

            odd = melhor["odd"]

            odd_justa = 1 / probabilidade

            ev = (
                (
                    probabilidade * odd
                ) - 1
            ) * 100

            if ev < EV_MINIMO:
                continue

            stake, kelly = calcular_stake(
                probabilidade,
                odd
            )

            oportunidades.append({

                "jogo": jogo,

                "mercado": mercado,

                "selecao": selecao,

                "casa": melhor["casa"],

                "odd": odd,

                "probabilidade":
                    probabilidade,

                "odd_justa":
                    odd_justa,

                "ev": ev,

                "stake":
                    stake,

                "kelly":
                    kelly

            })

    return oportunidades


# ============================================================
# ANALISAR OPORTUNIDADES
# ============================================================

def analisar_oportunidades():

    jogos = buscar_jogos_do_dia()

    print(
        f"⚽ Jogos encontrados: {len(jogos)}"
    )

    todas = []

    for jogo in jogos:

        oportunidades = analisar_jogo(jogo)

        todas.extend(oportunidades)

    todas.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    return todas[:20]


# ============================================================
# FORMATAR OPORTUNIDADE
# ============================================================

def formatar_oportunidade(o):

    jogo = o["jogo"]

    horario = formatar_data_hora(
        jogo["commence_time"]
    )

    probabilidade = (
        o["probabilidade"] * 100
    )

    return (

        "🔥 <b>OPORTUNIDADE EV+</b>\n\n"

        f"🏆 <b>LIGA:</b> "
        f"{jogo['league']}\n"

        f"⚽ <b>JOGO:</b> "
        f"{jogo['home_team']} x "
        f"{jogo['away_team']}\n"

        f"📅 <b>DATA/HORA:</b> "
        f"{horario} (Brasília)\n\n"

        f"🎯 <b>MERCADO:</b> "
        f"{nome_mercado(o['mercado'])}\n"

        f"🏦 <b>APOSTAR EM:</b> "
        f"{o['selecao']}\n"

        f"💼 <b>MELHOR CASA:</b> "
        f"{o['casa']}\n"

        f"💰 <b>MELHOR ODD:</b> "
        f"{o['odd']:.2f}\n"

        f"📊 <b>PROBABILIDADE:</b> "
        f"{probabilidade:.1f}%\n"

        f"📐 <b>ODD JUSTA:</b> "
        f"{o['odd_justa']:.2f}\n"

        f"📈 <b>EV:</b> "
        f"+{o['ev']:.2f}%\n"

        f"💵 <b>STAKE SUGERIDA:</b> "
        f"R$ {o['stake']:.2f}\n\n"

        "📌 Stake baseada em Kelly "
        "fracionado (25%).\n"

        "⚠️ Não é garantia de lucro."
    )


# ============================================================
# TELEGRAM
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN)


@bot.message_handler(commands=["start"])
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.reply_to(

        message,

        "✅ <b>BOT ATIVADO!</b>\n\n"

        "🕘 Análise automática: "
        "<b>09:00 Brasília</b>\n\n"

        "📅 Jogos do dia seguinte\n"

        "🏆 Principais ligas\n"

        "💼 Comparação entre casas\n"

        "📈 EV+\n"

        "💵 Stake sugerida\n\n"

        "📊 Use /odds para testar agora.",

        parse_mode="HTML"
    )


@bot.message_handler(commands=["odds"])
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(

        message.chat.id,

        "🔎 <b>ANÁLISE MANUAL</b>\n\n"

        "Comparando as odds disponíveis "
        "e analisando os jogos do dia seguinte...",

        parse_mode="HTML"
    )

    try:

        oportunidades = analisar_oportunidades()

        if not oportunidades:

            inicio, fim = periodo_analisado()

            data = inicio.strftime("%d/%m/%Y")

            bot.send_message(

                message.chat.id,

                "❌ Não encontrei oportunidades "
                f"com EV de pelo menos "
                f"{EV_MINIMO:.0f}% para "
                f"<b>{data}</b>.",

                parse_mode="HTML"
            )

            return

        bot.send_message(

            message.chat.id,

            f"🔥 <b>{len(oportunidades)}</b> "
            "oportunidades encontradas.",

            parse_mode="HTML"
        )

        for oportunidade in oportunidades:

            bot.send_message(

                message.chat.id,

                formatar_oportunidade(
                    oportunidade
                ),

                parse_mode="HTML"
            )

            time.sleep(1)

    except Exception as e:

        print(f"❌ Erro na análise: {e}")

        bot.send_message(

            message.chat.id,

            "❌ Ocorreu um erro durante "
            "a análise. Verifique os logs."
        )


@bot.message_handler(func=lambda message: True)
def qualquer_mensagem(message):

    bot.reply_to(

        message,

        "Use /start para ativar o bot "
        "ou /odds para analisar."
    )


# ============================================================
# AGENDADOR
# ============================================================

def verificar_horario():

    print(
        "⏰ Análise automática: 09:00 Brasília."
    )

    while True:

        agora = agora_brasilia()

        if (
            agora.hour == HORA_ANALISE
            and agora.minute == MINUTO_ANALISE
        ):

            try:
                enviar_analise_diaria()
            except Exception as e:
                print(
                    f"❌ Erro na análise automática: {e}"
                )

            time.sleep(70)

        time.sleep(20)


def enviar_analise_diaria():

    global ULTIMO_ENVIO

    if CHAT_ID is None:

        print("⚠️ CHAT_ID não configurado.")

        return

    hoje = agora_brasilia().date()

    if ULTIMO_ENVIO == hoje:
        return

    oportunidades = analisar_oportunidades()

    inicio, fim = periodo_analisado()

    data_analisada = inicio.strftime("%d/%m/%Y")

    if not oportunidades:

        bot.send_message(

            CHAT_ID,

            "📊 <b>ANÁLISE DIÁRIA</b>\n\n"

            f"📅 Jogos analisados: "
            f"{data_analisada}\n\n"

            f"❌ Não encontrei oportunidades "
            f"com EV de pelo menos "
            f"{EV_MINIMO:.0f}%.",

            parse_mode="HTML"
        )

        ULTIMO_ENVIO = hoje

        return

    bot.send_message(

        CHAT_ID,

        "📊 <b>ANÁLISE DIÁRIA — EV+</b>\n\n"

        f"📅 Jogos: {data_analisada}\n"

        "🕘 Análise: 09:00 Brasília\n"

        f"🔥 <b>{len(oportunidades)}</b> "
        "oportunidades encontradas.",

        parse_mode="HTML"
    )

    for oportunidade in oportunidades:

        try:

            bot.send_message(

                CHAT_ID,

                formatar_oportunidade(
                    oportunidade
                ),

                parse_mode="HTML"
            )

            time.sleep(1)

        except Exception as e:

            print(
                f"❌ Erro enviando: {e}"
            )

    ULTIMO_ENVIO = hoje


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    print("🚀 Iniciando sistema...")

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    Thread(
        target=verificar_horario,
        daemon=True
    ).start()

    print("🤖 Telegram conectado.")

    bot.infinity_polling()
