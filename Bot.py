import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# =========================================================
# CONFIGURAÇÕES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# EV mínimo para aparecer
EV_MINIMO = 3.0

# EV máximo para evitar discrepâncias exageradas
EV_MAXIMO = 20.0

# Número mínimo de casas comparadas
MINIMO_CASAS = 3

# Kelly fracionado
KELLY_FRACAO = 0.25

# Banca usada SOMENTE para calcular a sugestão de stake
BANCA = 1000.00

# Fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# Chat do Telegram
CHAT_ID = None


# =========================================================
# VERIFICAÇÃO DAS CHAVES
# =========================================================

if not BOT_TOKEN:
    print("ERRO: BOT_TOKEN não encontrado no Render.")

if not ODDS_API_KEY:
    print("ERRO: ODDS_API_KEY não encontrada no Render.")


# =========================================================
# TELEGRAM
# =========================================================

bot = telebot.TeleBot(BOT_TOKEN)


# =========================================================
# FLASK / RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():
    port = int(os.getenv("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# CAMPEONATOS
# =========================================================

CAMPEONATOS = {
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


# =========================================================
# MERCADOS
# =========================================================

MERCADOS = [
    "h2h",
    "totals",
    "btts"
]


# =========================================================
# BUSCAR ODDS
# =========================================================

def buscar_odds(esporte):

    url = f"https://api.the-odds-api.com/v4/sports/{esporte}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": ",".join(MERCADOS),
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(
            url,
            params=params,
            timeout=30
        )

        print(
            f"API {esporte}: HTTP {resposta.status_code}"
        )

        # Mostrar limite restante da API, quando disponível
        restante = resposta.headers.get(
            "x-requests-remaining"
        )

        usado = resposta.headers.get(
            "x-requests-used"
        )

        if restante:
            print(
                f"API: requisições restantes = {restante}"
            )

        if usado:
            print(
                f"API: requisições usadas = {usado}"
            )

        if resposta.status_code == 401:

            print(
                "ERRO 401: ODDS_API_KEY inválida ou não autorizada."
            )

            return []

        if resposta.status_code == 403:

            print(
                "ERRO 403: acesso negado pela Odds API."
            )

            return []

        if resposta.status_code == 422:

            print(
                f"ERRO 422: esporte/mercado inválido: {esporte}"
            )

            return []

        if resposta.status_code != 200:

            print(
                "ERRO API:",
                resposta.text[:500]
            )

            return []

        return resposta.json()

    except Exception as erro:

        print(
            f"Erro ao consultar {esporte}: {erro}"
        )

        return []


# =========================================================
# CRIAR IDENTIFICAÇÃO DO RESULTADO
# =========================================================

def chave_resultado(mercado, outcome):

    nome = outcome.get("name")

    # Totals precisa considerar o ponto.
    # Exemplo:
    # Over 2.5 é diferente de Over 3.5
    if mercado == "totals":

        ponto = outcome.get("point")

        if ponto is not None:

            return f"{nome} {ponto}"

    return nome


# =========================================================
# COLETAR RESULTADOS DE UM MERCADO
# =========================================================

def coletar_mercado(bookmakers, mercado_alvo):

    resultados = {}

    for bookmaker in bookmakers:

        nome_casa = bookmaker.get(
            "title",
            "Casa"
        )

        for mercado in bookmaker.get("markets", []):

            if mercado.get("key") != mercado_alvo:
                continue

            outcomes = mercado.get(
                "outcomes",
                []
            )

            if not outcomes:
                continue

            for outcome in outcomes:

                odd = outcome.get("price")

                if not odd or odd <= 1:
                    continue

                chave = chave_resultado(
                    mercado_alvo,
                    outcome
                )

                if not chave:
                    continue

                if chave not in resultados:

                    resultados[chave] = []

                resultados[chave].append({
                    "casa": nome_casa,
                    "odd": float(odd)
                })

    return resultados


# =========================================================
# PROBABILIDADE SEM MARGEM
# =========================================================

def probabilidade_sem_margem(outcomes):

    if not outcomes:
        return None

    probabilidades = []

    for item in outcomes:

        odd = item["odd"]

        if odd > 1:

            probabilidades.append(
                1 / odd
            )

    if not probabilidades:
        return None

    soma = sum(probabilidades)

    if soma <= 0:
        return None

    # Retira aproximadamente a margem da casa
    probabilidades_sem_margem = [
        p / soma
        for p in probabilidades
    ]

    return probabilidades_sem_margem


# =========================================================
# MEDIANA
# =========================================================

def mediana(lista):

    if not lista:
        return None

    valores = sorted(lista)

    tamanho = len(valores)

    meio = tamanho // 2

    if tamanho % 2 == 1:

        return valores[meio]

    return (
        valores[meio - 1]
        + valores[meio]
    ) / 2


# =========================================================
# ANALISAR MERCADO
# =========================================================

def analisar_mercado(bookmakers, mercado):

    dados = coletar_mercado(
        bookmakers,
        mercado
    )

    oportunidades = []

    for resultado, ofertas in dados.items():

        # Precisamos de pelo menos X casas
        if len(ofertas) < MINIMO_CASAS:
            continue

        probabilidades = probabilidade_sem_margem(
            ofertas
        )

        if not probabilidades:
            continue

        # Mediana para diminuir influência de extremos
        probabilidade = mediana(
            probabilidades
        )

        if not probabilidade:
            continue

        # Melhor odd disponível
        melhor = max(
            ofertas,
            key=lambda x: x["odd"]
        )

        melhor_odd = melhor["odd"]
        melhor_casa = melhor["casa"]

        # Odd justa
        odd_justa = 1 / probabilidade

        # EV
        ev = (
            (probabilidade * melhor_odd) - 1
        ) * 100

        # Filtro EV
        if ev < EV_MINIMO:
            continue

        if ev > EV_MAXIMO:
            continue

        # Melhor odd precisa ser maior que a justa
        if melhor_odd <= odd_justa:
            continue

        # =================================================
        # KELLY
        # =================================================

        b = melhor_odd - 1
        q = 1 - probabilidade

        kelly = (
            (b * probabilidade) - q
        ) / b

        if kelly <= 0:
            continue

        stake = (
            BANCA
            * kelly
            * KELLY_FRACAO
        )

        # Limite máximo de 5% da banca
        limite_stake = BANCA * 0.05

        if stake > limite_stake:

            stake = limite_stake

        oportunidades.append({

            "mercado": mercado,

            "resultado": resultado,

            "odd": melhor_odd,

            "casa": melhor_casa,

            "probabilidade": probabilidade,

            "odd_justa": odd_justa,

            "ev": ev,

            "stake": stake,

            "casas": len(ofertas)
        })

    return oportunidades


# =========================================================
# NOME DOS MERCADOS
# =========================================================

def nome_mercado(mercado):

    if mercado == "h2h":
        return "Vencedor da partida (1X2)"

    if mercado == "totals":
        return "Mais/Menos gols"

    if mercado == "btts":
        return "Ambas marcam"

    return mercado


# =========================================================
# ANALISAR TODOS OS CAMPEONATOS
# =========================================================

def analisar():

    agora = datetime.now(TZ)

    amanha = (
        agora.date()
        + timedelta(days=1)
    )

    print("")
    print("==============================")
    print("INICIANDO ANÁLISE")
    print("==============================")
    print(
        "Data atual:",
        agora.strftime("%d/%m/%Y %H:%M")
    )
    print(
        "Procurando jogos de:",
        amanha.strftime("%d/%m/%Y")
    )

    oportunidades = []

    total_jogos_api = 0
    total_jogos_amanha = 0

    for esporte, liga in CAMPEONATOS.items():

        eventos = buscar_odds(esporte)

        print(
            f"{liga}: {len(eventos)} jogos retornados pela API"
        )

        total_jogos_api += len(eventos)

        for evento in eventos:

            data_evento = evento.get(
                "commence_time"
            )

            if not data_evento:
                continue

            try:

                data = datetime.fromisoformat(
                    data_evento.replace(
                        "Z",
                        "+00:00"
                    )
                ).astimezone(TZ)

            except Exception:

                continue

            # Somente amanhã
            if data.date() != amanha:

                continue

            total_jogos_amanha += 1

            home = evento.get(
                "home_team",
                ""
            )

            away = evento.get(
                "away_team",
                ""
            )

            bookmakers = evento.get(
                "bookmakers",
                []
            )

            print(
                f"JOGO: {home} x {away} | "
                f"Casas: {len(bookmakers)}"
            )

            if len(bookmakers) < MINIMO_CASAS:

                print(
                    "  -> descartado: poucas casas"
                )

                continue

            for mercado in MERCADOS:

                analises = analisar_mercado(
                    bookmakers,
                    mercado
                )

                for analise in analises:

                    analise["liga"] = liga
                    analise["home"] = home
                    analise["away"] = away
                    analise["data"] = data

                    oportunidades.append(
                        analise
                    )

    # =====================================================
    # ORDENAÇÃO
    # =====================================================

    oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    print("")
    print("==============================")
    print("RESULTADO DA ANÁLISE")
    print("==============================")

    print(
        "Jogos retornados pela API:",
        total_jogos_api
    )

    print(
        "Jogos para amanhã:",
        total_jogos_amanha
    )

    print(
        "Oportunidades encontradas:",
        len(oportunidades)
    )

    # =====================================================
    # EVITAR REPETIÇÃO EXCESSIVA
    # =====================================================

    finais = []

    combinacoes = set()

    for oportunidade in oportunidades:

        chave = (
            oportunidade["home"],
            oportunidade["away"],
            oportunidade["mercado"],
            oportunidade["resultado"]
        )

        if chave in combinacoes:

            continue

        combinacoes.add(chave)

        finais.append(
            oportunidade
        )

        # Máximo de 15 mensagens
        if len(finais) >= 15:

            break

    return finais


# =========================================================
# ENVIAR ANÁLISE
# =========================================================

def enviar_analise():

    global CHAT_ID

    if CHAT_ID is None:

        print(
            "CHAT_ID ainda não definido."
        )

        return

    try:

        oportunidades = analisar()

        if not oportunidades:

            bot.send_message(
                CHAT_ID,
                "🔎 Nenhuma oportunidade EV+ encontrada para amanhã dentro dos filtros definidos.\n\n"
                "📊 Verifique os Logs do Render para ver quantos jogos e casas foram encontrados."
            )

            return

        bot.send_message(
            CHAT_ID,
            f"📊 *{len(oportunidades)} oportunidades encontradas.*",
            parse_mode="Markdown"
        )

        for op in oportunidades:

            data = op["data"]

            mensagem = (

                "🔥 *OPORTUNIDADE EV+*\n\n"

                f"🏆 *LIGA:* {op['liga']}\n"

                f"⚽ *JOGO:* "
                f"{op['home']} x {op['away']}\n"

                f"📅 *DATA/HORA:* "
                f"{data.strftime('%d/%m/%Y às %H:%M')} "
                f"(Brasília)\n\n"

                f"🎯 *MERCADO:* "
                f"{nome_mercado(op['mercado'])}\n"

                f"🏦 *RESULTADO:* "
                f"{op['resultado']}\n"

                f"💼 *MELHOR CASA:* "
                f"{op['casa']}\n"

                f"💰 *MELHOR ODD:* "
                f"{op['odd']:.2f}\n\n"

                f"📊 *PROBABILIDADE ESTIMADA:* "
                f"{op['probabilidade'] * 100:.1f}%\n"

                f"📐 *ODD JUSTA:* "
                f"{op['odd_justa']:.2f}\n"

                f"📈 *EV ESTIMADO:* "
                f"+{op['ev']:.2f}%\n"

                f"💵 *STAKE SUGERIDA:* "
                f"R$ {op['stake']:.2f}\n\n"

                f"🏦 *CASAS ANALISADAS:* "
                f"{op['casas']}\n\n"

                "📌 Kelly fracionado: 25%\n"
                "⚠️ EV é uma estimativa baseada nas odds disponíveis e não garante lucro."
            )

            bot.send_message(
                CHAT_ID,
                mensagem,
                parse_mode="Markdown"
            )

            time.sleep(0.5)

    except Exception as erro:

        print(
            "ERRO NA ANÁLISE:",
            erro
        )

        try:

            bot.send_message(
                CHAT_ID,
                "❌ Ocorreu um erro durante a análise. Veja os Logs do Render."
            )

        except Exception:

            pass


# =========================================================
# /START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🤖 *Bot conectado!*\n\n"
        "Use /odds para analisar as oportunidades de amanhã.",
        parse_mode="Markdown"
    )


# =========================================================
# /ODDS
# =========================================================

@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🔎 Analisando as odds disponíveis...\n\n"
        "Aguarde alguns segundos."
    )

    Thread(
        target=enviar_analise
    ).start()


# =========================================================
# OUTRAS MENSAGENS
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def qualquer_mensagem(message):

    bot.send_message(
        message.chat.id,
        "Use /odds para executar uma análise."
    )


# =========================================================
# INICIAR BOT
# =========================================================

if __name__ == "__main__":

    print("")
    print("==============================")
    print("BOT INICIANDO")
    print("==============================")

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    print(
        "Servidor Flask iniciado."
    )

    print(
        "Análise automática DESATIVADA."
    )

    print(
        "Use /odds no Telegram para analisar."
    )

    print("==============================")

    bot.infinity_polling(
        skip_pending=True
    )
