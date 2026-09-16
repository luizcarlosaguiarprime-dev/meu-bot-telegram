import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0

# Horário da análise diária
HORA_ANALISE = 9
MINUTO_ANALISE = 0

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

# Chat que receberá as oportunidades
CHAT_ID = None

# Evita enviar duas vezes no mesmo dia
ULTIMO_ENVIO = None


# ============================================================
# COMPETIÇÕES PRINCIPAIS
# ============================================================

ESPORTES = [
    "soccer_epl",              # Premier League
    "soccer_spain_la_liga",    # La Liga
    "soccer_italy_serie_a",    # Serie A
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
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():
    app.run(host="0.0.0.0", port=10000)


# ============================================================
# DATA E HORÁRIO
# ============================================================

def agora_brasilia():
    return datetime.now(FUSO_BRASILIA)


def formatar_data_hora(utc_time):
    try:
        dt = datetime.fromisoformat(
            utc_time.replace("Z", "+00:00")
        )

        dt_brasilia = dt.astimezone(FUSO_BRASILIA)

        return dt_brasilia.strftime("%d/%m/%Y às %H:%M")

    except Exception:
        return "Horário não informado"


# ============================================================
# NOME DOS MERCADOS
# ============================================================

def nome_mercado(market_key):

    if market_key == "h2h":
        return "Vencedor da partida (1X2)"

    if market_key == "totals":
        return "Mais/Menos 2,5 gols"

    if market_key == "btts":
        return "Ambas Marcam"

    return market_key


# ============================================================
# BUSCAR ODDS
# ============================================================

def buscar_odds(esporte, mercado):

    url = f"https://api.the-odds-api.com/v4/sports/{esporte}/odds"

    parametros = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": mercado,
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(
            url,
            params=parametros,
            timeout=30
        )

        if resposta.status_code == 422:

            print(
                f"Mercado {mercado} não disponível para {esporte}"
            )

            return []

        resposta.raise_for_status()

        return resposta.json()

    except Exception as e:

        print(
            f"Erro buscando {esporte} / {mercado}: {e}"
        )

        return []


# ============================================================
# BUSCAR JOGOS DO DIA
# ============================================================

def buscar_jogos_do_dia():

    jogos = {}

    agora = agora_brasilia()

    inicio_dia = agora.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    fim_dia = inicio_dia + timedelta(days=1)

    for esporte in ESPORTES:

        nome_liga = NOMES_LIGAS.get(
            esporte,
            esporte
        )

        for mercado in MERCADOS:

            dados = buscar_odds(
                esporte,
                mercado
            )

            for jogo in dados:

                try:

                    horario = datetime.fromisoformat(
                        jogo["commence_time"].replace(
                            "Z",
                            "+00:00"
                        )
                    )

                    horario_brasilia = horario.astimezone(
                        FUSO_BRASILIA
                    )

                    # Somente jogos do dia
                    if not (
                        inicio_dia
                        <= horario_brasilia
                        < fim_dia
                    ):
                        continue

                    jogo_id = jogo["id"]

                    if jogo_id not in jogos:

                        jogos[jogo_id] = {
                            "id": jogo_id,
                            "sport_key": esporte,
                            "league": nome_liga,
                            "home_team": jogo["home_team"],
                            "away_team": jogo["away_team"],
                            "commence_time": jogo["commence_time"],
                            "bookmakers": []
                        }

                    # Adiciona as casas encontradas
                    for bookmaker in jogo.get(
                        "bookmakers",
                        []
                    ):

                        bookmaker_copy = dict(bookmaker)

                        bookmaker_copy[
                            "_mercado"
                        ] = mercado

                        jogos[jogo_id][
                            "bookmakers"
                        ].append(bookmaker_copy)

                except Exception as e:

                    print(
                        f"Erro processando jogo: {e}"
                    )

    return list(jogos.values())


# ============================================================
# CALCULAR CONSENSO DO MERCADO
# ============================================================

def calcular_consenso(bookmakers, mercado):

    probabilidades = {}

    for bookmaker in bookmakers:

        mercados = bookmaker.get(
            "markets",
            []
        )

        for market in mercados:

            if market.get("key") != mercado:
                continue

            outcomes = market.get(
                "outcomes",
                []
            )

            if not outcomes:
                continue

            # Soma das probabilidades implícitas
            soma = 0

            for outcome in outcomes:

                odd = outcome.get("price")

                if odd and odd > 0:
                    soma += 1 / odd

            if soma <= 0:
                continue

            # Remove a margem da casa
            for outcome in outcomes:

                nome = outcome.get(
                    "name"
                )

                odd = outcome.get(
                    "price"
                )

                if not odd or odd <= 0:
                    continue

                prob = (1 / odd) / soma

                if nome not in probabilidades:
                    probabilidades[nome] = []

                probabilidades[nome].append(
                    prob
                )

    consenso = {}

    for nome, valores in probabilidades.items():

        if valores:

            consenso[nome] = (
                sum(valores) / len(valores)
            )

    return consenso


# ============================================================
# CALCULAR EV
# ============================================================

def calcular_ev(probabilidade, odd):

    return (
        (probabilidade * odd) - 1
    ) * 100


# ============================================================
# ANALISAR UM JOGO
# ============================================================

def analisar_jogo(jogo):

    oportunidades = []

    bookmakers = jogo.get(
        "bookmakers",
        []
    )

    mercados_encontrados = set()

    for bookmaker in bookmakers:

        for market in bookmaker.get(
            "markets",
            []
        ):

            mercados_encontrados.add(
                market.get("key")
            )

    for mercado in mercados_encontrados:

        consenso = calcular_consenso(
            bookmakers,
            mercado
        )

        if not consenso:
            continue

        for bookmaker in bookmakers:

            for market in bookmaker.get(
                "markets",
                []
            ):

                if market.get("key") != mercado:
                    continue

                for outcome in market.get(
                    "outcomes",
                    []
                ):

                    nome = outcome.get(
                        "name"
                    )

                    odd = outcome.get(
                        "price"
                    )

                    if (
                        not nome
                        or not odd
                        or odd <= 1
                    ):
                        continue

                    probabilidade = consenso.get(
                        nome
                    )

                    if not probabilidade:
                        continue

                    odd_justa = (
                        1 / probabilidade
                    )

                    ev = calcular_ev(
                        probabilidade,
                        odd
                    )

                    if ev >= EV_MINIMO:

                        oportunidades.append({

                            "jogo": jogo,

                            "mercado": mercado,

                            "selecao": nome,

                            "casa": bookmaker.get(
                                "title",
                                "Casa desconhecida"
                            ),

                            "odd": odd,

                            "probabilidade":
                                probabilidade,

                            "odd_justa":
                                odd_justa,

                            "ev": ev

                        })

    return oportunidades


# ============================================================
# ANALISAR TODOS OS JOGOS
# ============================================================

def analisar_oportunidades():

    print(
        "🔎 Iniciando análise diária..."
    )

    jogos = buscar_jogos_do_dia()

    print(
        f"⚽ Jogos encontrados: {len(jogos)}"
    )

    todas = []

    for jogo in jogos:

        oportunidades = analisar_jogo(
            jogo
        )

        todas.extend(
            oportunidades
        )

    # Ordena pelas maiores oportunidades
    todas.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # Remove oportunidades praticamente iguais
    resultado = []

    chaves = set()

    for oportunidade in todas:

        jogo = oportunidade["jogo"]

        chave = (
            jogo["id"],
            oportunidade["mercado"],
            oportunidade["selecao"]
        )

        if chave in chaves:
            continue

        chaves.add(chave)

        resultado.append(
            oportunidade
        )

    # Limita às principais oportunidades
    return resultado[:20]


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

        f"💼 <b>CASA:</b> "
        f"{o['casa']}\n"

        f"💰 <b>ODD:</b> "
        f"{o['odd']:.2f}\n"

        f"📊 <b>PROBABILIDADE:</b> "
        f"{probabilidade:.1f}%\n"

        f"📐 <b>ODD JUSTA:</b> "
        f"{o['odd_justa']:.2f}\n"

        f"📈 <b>EV:</b> "
        f"+{o['ev']:.2f}%\n\n"

        "⚠️ EV calculado com base no "
        "consenso das odds disponíveis."
    )


# ============================================================
# ENVIO DIÁRIO
# ============================================================

def enviar_analise_diaria():

    global ULTIMO_ENVIO

    if CHAT_ID is None:

        print(
            "Nenhum CHAT_ID definido. "
            "Envie /start no Telegram."
        )

        return

    hoje = agora_brasilia().date()

    if ULTIMO_ENVIO == hoje:

        print(
            "Análise de hoje já foi enviada."
        )

        return

    oportunidades = analisar_oportunidades()

    if not oportunidades:

        bot.send_message(
            CHAT_ID,
            "📊 <b>Análise diária</b>\n\n"
            "Hoje não encontrei oportunidades "
            f"com EV de pelo menos {EV_MINIMO:.0f}% "
            "nas principais ligas analisadas.",
            parse_mode="HTML"
        )

        ULTIMO_ENVIO = hoje

        return

    mensagem_inicial = (
        "📊 <b>ANÁLISE DIÁRIA — EV+</b>\n\n"
        "🕘 Análise realizada às 09:00 "
        "(Brasília)\n"
        "⚽ Jogos de hoje\n"
        "🏆 Principais ligas nacionais "
        "e continentais\n\n"
        f"🔥 Foram encontradas "
        f"<b>{len(oportunidades)}</b> "
        "oportunidades.\n\n"
        "Principais oportunidades:"
    )

    bot.send_message(
        CHAT_ID,
        mensagem_inicial,
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
                f"Erro enviando oportunidade: {e}"
            )

    ULTIMO_ENVIO = hoje

    print(
        "✅ Análise diária enviada."
    )


# ============================================================
# AGENDADOR
# ============================================================

def verificar_horario():

    print(
        "⏰ Agendador diário iniciado."
    )

    while True:

        agora = agora_brasilia()

        if (
            agora.hour == HORA_ANALISE
            and agora.minute == MINUTO_ANALISE
        ):

            enviar_analise_diaria()

            # Evita executar várias vezes
            # durante o mesmo minuto
            time.sleep(65)

        time.sleep(20)


# ============================================================
# TELEGRAM
# ============================================================

bot = telebot.TeleBot(
    BOT_TOKEN
)


@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.reply_to(
        message,
        "✅ <b>Bot conectado!</b>\n\n"
        "📊 A análise será feita "
        "<b>uma vez por dia</b>, "
        "às <b>09:00</b> no horário de Brasília.\n\n"
        "⚽ Vou analisar os jogos do dia "
        "das principais ligas nacionais "
        "e continentais.\n\n"
        "🔥 As oportunidades serão "
        "enviadas automaticamente para você.",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🔎 Analisando as principais ligas "
        "e os jogos de hoje..."
    )

    oportunidades = analisar_oportunidades()

    if not oportunidades:

        bot.send_message(
            message.chat.id,
            "❌ Não encontrei oportunidades "
            f"com EV de pelo menos {EV_MINIMO:.0f}% "
            "nos jogos de hoje."
        )

        return

    bot.send_message(
        message.chat.id,
        f"🔥 Encontrei <b>{len(oportunidades)}</b> "
        "oportunidades EV+.",
        parse_mode="HTML"
    )

    for oportunidade in oportunidades:

        try:

            bot.send_message(
                message.chat.id,
                formatar_oportunidade(
                    oportunidade
                ),
                parse_mode="HTML"
            )

            time.sleep(1)

        except Exception as e:

            print(
                f"Erro enviando oportunidade: {e}"
            )


@bot.message_handler(
    func=lambda message: True
)
def qualquer_mensagem(message):

    bot.reply_to(
        message,
        "Use /start para ativar as análises "
        "diárias ou /odds para analisar agora."
    )


# ============================================================
# INICIAR SISTEMA
# ============================================================

if __name__ == "__main__":

    print(
        "🚀 Iniciando bot..."
    )

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    Thread(
        target=verificar_horario,
        daemon=True
    ).start()

    print(
        "🤖 Bot Telegram iniciado."
    )

    bot.infinity_polling()
