import os
import time
import threading
from datetime import datetime, timedelta

import requests
import telebot
from flask import Flask, jsonify


# =========================================================
# CONFIGURAÇÃO
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

HOST = "sportapi7.p.rapidapi.com"

INTERVALO_LIVE = 60
HISTORICO_JOGOS = 15
COOLDOWN_ALERTA = 15 * 60

CHAT_ID = None
MONITORANDO = True

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN não configurado.")

if not RAPIDAPI_KEY:
    raise Exception("RAPIDAPI_KEY não configurada.")


# =========================================================
# COMPETIÇÕES PERMITIDAS
# =========================================================

LIGAS_PERMITIDAS = {
    "brasileirao": [
        "brasileirão",
        "brasileirao",
        "serie a",
        "brazil serie a",
        "brazilian serie a",
    ],

    "premier": [
        "premier league",
        "english premier league",
    ],

    "la_liga": [
        "la liga",
        "laliga",
        "primera division",
        "primera división",
    ],

    "bundesliga": [
        "bundesliga",
        "german bundesliga",
    ],

    "serie_a": [
        "serie a",
        "italian serie a",
    ],

    "ligue_1": [
        "ligue 1",
        "ligue one",
        "french ligue 1",
    ],

    "champions": [
        "champions league",
        "uefa champions league",
    ],

    "europa": [
        "europa league",
        "uefa europa league",
    ],

    "conference": [
        "conference league",
        "uefa conference league",
    ],

    "libertadores": [
        "libertadores",
        "copa libertadores",
        "conmebol libertadores",
    ],

    "sudamericana": [
        "sudamericana",
        "copa sudamericana",
        "conmebol sudamericana",
    ],
}


# =========================================================
# BOT / FLASK
# =========================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

session = requests.Session()

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": HOST,
    "Accept": "application/json",
}


# =========================================================
# CONTROLE DE ALERTAS
# =========================================================

ultimo_alerta = {}


# =========================================================
# UTILIDADES
# =========================================================

def agora_local():
    return datetime.now()


def normalizar(texto):
    if texto is None:
        return ""

    return (
        str(texto)
        .lower()
        .strip()
        .replace("á", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


def nome_time(time_data):
    if not isinstance(time_data, dict):
        return "Desconhecido"

    return (
        time_data.get("name")
        or time_data.get("shortName")
        or time_data.get("slug")
        or "Desconhecido"
    )


def id_time(time_data):
    if not isinstance(time_data, dict):
        return None

    return time_data.get("id")


def extrair_times(evento):
    home = (
        evento.get("homeTeam")
        or evento.get("home")
        or {}
    )

    away = (
        evento.get("awayTeam")
        or evento.get("away")
        or {}
    )

    return home, away


def event_id(evento):
    return (
        evento.get("id")
        or evento.get("eventId")
    )


# =========================================================
# API
# =========================================================

def api_get(endpoint, params=None):

    url = f"https://{HOST}{endpoint}"

    try:

        resposta = session.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=20
        )

        if resposta.status_code == 200:

            try:
                return resposta.json()
            except Exception:
                return None

        print(
            f"API {resposta.status_code}: {endpoint}"
        )

        return None

    except Exception as erro:

        print(
            f"ERRO API: {erro}"
        )

        return None


def extrair_lista(dados):

    if not dados:
        return []

    if isinstance(dados, list):
        return dados

    if isinstance(dados, dict):

        for chave in [
            "events",
            "data",
            "results",
            "items",
        ]:

            valor = dados.get(chave)

            if isinstance(valor, list):
                return valor

    return []


# =========================================================
# IDENTIFICAR COMPETIÇÃO
# =========================================================

def dados_torneio(evento):

    torneio = (
        evento.get("tournament")
        or evento.get("league")
        or evento.get("competition")
        or {}
    )

    if not isinstance(torneio, dict):
        return "", ""

    nome = (
        torneio.get("name")
        or torneio.get("uniqueName")
        or torneio.get("slug")
        or ""
    )

    categoria = torneio.get("category") or {}

    if isinstance(categoria, dict):

        categoria_nome = (
            categoria.get("name")
            or categoria.get("slug")
            or ""
        )

    else:
        categoria_nome = str(categoria)

    return nome, categoria_nome


def liga_permitida(evento):

    nome, categoria = dados_torneio(evento)

    texto = normalizar(
        f"{nome} {categoria}"
    )

    for grupo, nomes in LIGAS_PERMITIDAS.items():

        for nome_liga in nomes:

            if normalizar(nome_liga) in texto:

                return True, grupo

    return False, None


# =========================================================
# JOGOS AO VIVO
# =========================================================

def obter_jogos_live():

    candidatos = [

        "/api/v1/sport/football/live",

        "/api/v1/sport/football/events/live",

    ]

    for endpoint in candidatos:

        dados = api_get(endpoint)

        jogos = extrair_lista(dados)

        if jogos:
            return jogos

    return []


# =========================================================
# JOGOS POR DATA
# =========================================================

def obter_jogos_data(data):

    candidatos = [

        f"/api/v1/sport/football/scheduled-events/{data}",

        f"/api/v1/sport/football/events/{data}",

    ]

    for endpoint in candidatos:

        dados = api_get(endpoint)

        jogos = extrair_lista(dados)

        if jogos:
            return jogos

    return []


# =========================================================
# EVENTO INDIVIDUAL
# =========================================================

def obter_evento(event_id_value):

    if not event_id_value:
        return None

    dados = api_get(
        f"/api/v1/event/{event_id_value}"
    )

    if isinstance(dados, dict):

        if "event" in dados:
            return dados["event"]

        if "data" in dados and isinstance(
            dados["data"],
            dict
        ):
            return dados["data"]

        return dados

    return None


# =========================================================
# ESTATÍSTICAS LIVE
# =========================================================

def obter_estatisticas(event_id_value):

    candidatos = [

        f"/api/v1/event/{event_id_value}/statistics",

        f"/api/v1/event/{event_id_value}/statistics/periods",

    ]

    for endpoint in candidatos:

        dados = api_get(endpoint)

        if dados:
            return dados

    return None


# =========================================================
# SHOTMAP
# =========================================================

def obter_shotmap(event_id_value, team_id):

    if not event_id_value or not team_id:
        return None

    endpoint = (
        f"/api/v1/event/{event_id_value}"
        f"/shotmap/{team_id}"
    )

    return api_get(endpoint)


# =========================================================
# HISTÓRICO
# =========================================================

def obter_historico_time(team_id):

    if not team_id:
        return []

    candidatos = [

        f"/api/v1/team/{team_id}/events/last/0",

        f"/api/v1/team/{team_id}/matches/previous/0",

        f"/api/v1/team/{team_id}/events/last/1",

    ]

    for endpoint in candidatos:

        dados = api_get(endpoint)

        jogos = extrair_lista(dados)

        if jogos:
            return jogos[:HISTORICO_JOGOS]

    return []


# =========================================================
# STATUS LIVE
# =========================================================

def texto_status(evento):

    status = evento.get("status", {})

    if isinstance(status, dict):

        return normalizar(
            status.get("type")
            or status.get("description")
            or ""
        )

    return normalizar(status)


def esta_live(evento):

    status = texto_status(evento)

    palavras = [

        "inprogress",
        "in_progress",
        "live",
        "halftime",
        "1h",
        "2h",
        "firsthalf",
        "secondhalf",
        "extratime",
        "overtime",

    ]

    return any(
        palavra in status
        for palavra in palavras
    )


# =========================================================
# PLACAR
# =========================================================

def obter_placar(evento):

    home_score = evento.get(
        "homeScore",
        {}
    )

    away_score = evento.get(
        "awayScore",
        {}
    )

    if isinstance(home_score, dict):

        home = (
            home_score.get("current")
            if home_score.get("current") is not None
            else home_score.get("display", 0)
        )

    else:
        home = home_score or 0

    if isinstance(away_score, dict):

        away = (
            away_score.get("current")
            if away_score.get("current") is not None
            else away_score.get("display", 0)
        )

    else:
        away = away_score or 0

    return home or 0, away or 0


# =========================================================
# MINUTO DA PARTIDA
# =========================================================

def obter_minuto(evento):

    status = evento.get("status", {})

    if not isinstance(status, dict):
        return ""

    for chave in [
        "currentPeriodStartTimestamp",
        "periodStartTimestamp",
    ]:

        timestamp = status.get(chave)

        if timestamp:

            try:

                agora = time.time()

                minutos = int(
                    (agora - timestamp) / 60
                )

                if 0 <= minutos <= 130:
                    return f"{minutos}'"

            except Exception:
                pass

    descricao = (
        status.get("description")
        or status.get("type")
        or ""
    )

    return str(descricao)


# =========================================================
# BUSCA RECURSIVA DE NÚMEROS
# =========================================================

def coletar_itens(obj, resultado=None):

    if resultado is None:
        resultado = []

    if isinstance(obj, dict):

        # Alguns retornos da API possuem:
        # name, value, home, away

        nome = (
            obj.get("name")
            or obj.get("label")
            or obj.get("title")
            or obj.get("statType")
            or ""
        )

        if nome:

            resultado.append({
                "nome": normalizar(nome),
                "nome_original": str(nome),
                "home": obj.get("home"),
                "away": obj.get("away"),
                "valor": obj.get("value"),
            })

        for valor in obj.values():
            coletar_itens(valor, resultado)

    elif isinstance(obj, list):

        for item in obj:
            coletar_itens(item, resultado)

    return resultado


# =========================================================
# CONVERTER NÚMERO
# =========================================================

def numero(valor):

    if valor is None:
        return None

    if isinstance(valor, bool):
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor)

    texto = (
        texto
        .replace("%", "")
        .replace(",", ".")
    )

    try:
        return float(texto)

    except Exception:
        return None


# =========================================================
# EXTRAIR ESTATÍSTICAS
# =========================================================

def extrair_stats(dados):

    stats = {
        "posse_home": None,
        "posse_away": None,

        "chutes_home": None,
        "chutes_away": None,

        "alvo_home": None,
        "alvo_away": None,

        "escanteios_home": None,
        "escanteios_away": None,

        "ataques_perigosos_home": None,
        "ataques_perigosos_away": None,

        "recuperacoes_home": None,
        "recuperacoes_away": None,
    }

    itens = coletar_itens(dados)

    for item in itens:

        nome = item["nome"]

        home = numero(item.get("home"))
        away = numero(item.get("away"))

        if home is None and away is None:
            continue

        # POSSE
        if (
            "possession" in nome
            or "posse" in nome
        ):

            stats["posse_home"] = home
            stats["posse_away"] = away

        # FINALIZAÇÕES
        elif (
            "total shots" in nome
            or "total shots" == nome
            or "shots total" in nome
            or "finalizacoes" in nome
            or "finalizações" in nome
        ):

            stats["chutes_home"] = home
            stats["chutes_away"] = away

        # NO ALVO
        elif (
            "shots on target" in nome
            or "shots on goal" in nome
            or "chutes no alvo" in nome
            or "on target" in nome
        ):

            stats["alvo_home"] = home
            stats["alvo_away"] = away

        # ESCANTEIOS
        elif (
            "corner" in nome
            or "escanteio" in nome
            or "escanteios" in nome
        ):

            stats["escanteios_home"] = home
            stats["escanteios_away"] = away

        # ATAQUES PERIGOSOS
        elif (
            "dangerous attack" in nome
            or "dangerous attacks" in nome
            or "ataques perigosos" in nome
        ):

            stats["ataques_perigosos_home"] = home
            stats["ataques_perigosos_away"] = away

        # RECUPERAÇÃO
        elif (
            "ball recovery" in nome
            or "recoveries" in nome
            or "recuperacoes" in nome
            or "recuperações" in nome
        ):

            stats["recuperacoes_home"] = home
            stats["recuperacoes_away"] = away

    return stats


# =========================================================
# VALOR SEGURO
# =========================================================

def seguro(valor):

    if valor is None:
        return 0

    return float(valor)


# =========================================================
# ÍNDICE DE PRESSÃO
# =========================================================

def calcular_pressao(stats):

    pontos_home = 0
    pontos_away = 0

    # -----------------------------------------------------
    # POSSE
    # -----------------------------------------------------

    ph = stats["posse_home"]
    pa = stats["posse_away"]

    if ph is not None and pa is not None:

        if ph > pa:
            pontos_home += min(
                20,
                max(0, ph - pa) * 0.7
            )

        elif pa > ph:
            pontos_away += min(
                20,
                max(0, pa - ph) * 0.7
            )

    # -----------------------------------------------------
    # FINALIZAÇÕES
    # -----------------------------------------------------

    ch = stats["chutes_home"]
    ca = stats["chutes_away"]

    if ch is not None and ca is not None:

        if ch > ca:
            pontos_home += min(
                25,
                (ch - ca) * 2
            )

        elif ca > ch:
            pontos_away += min(
                25,
                (ca - ch) * 2
            )

    # -----------------------------------------------------
    # FINALIZAÇÕES NO ALVO
    # -----------------------------------------------------

    ah = stats["alvo_home"]
    aa = stats["alvo_away"]

    if ah is not None and aa is not None:

        if ah > aa:
            pontos_home += min(
                25,
                (ah - aa) * 4
            )

        elif aa > ah:
            pontos_away += min(
                25,
                (aa - ah) * 4
            )

    # -----------------------------------------------------
    # ESCANTEIOS
    # -----------------------------------------------------

    eh = stats["escanteios_home"]
    ea = stats["escanteios_away"]

    if eh is not None and ea is not None:

        if eh > ea:
            pontos_home += min(
                20,
                (eh - ea) * 3
            )

        elif ea > eh:
            pontos_away += min(
                20,
                (ea - eh) * 3
            )

    # -----------------------------------------------------
    # ATAQUES PERIGOSOS
    # -----------------------------------------------------

    dh = stats["ataques_perigosos_home"]
    da = stats["ataques_perigosos_away"]

    if dh is not None and da is not None:

        if dh > da:
            pontos_home += min(
                20,
                (dh - da) * 0.5
            )

        elif da > dh:
            pontos_away += min(
                20,
                (da - dh) * 0.5
            )

    home = min(100, int(pontos_home))
    away = min(100, int(pontos_away))

    return home, away


# =========================================================
# TENDÊNCIA DE ESCANTEIOS
# =========================================================

def tendencia_escanteios(stats):

    home = stats["escanteios_home"]
    away = stats["escanteios_away"]

    if home is None or away is None:
        return None

    total = home + away

    if total >= 8:
        return "ALTA"

    if total >= 5:
        return "MODERADA"

    return "BAIXA"


# =========================================================
# TENDÊNCIA DE FINALIZAÇÕES
# =========================================================

def tendencia_finalizacoes(stats):

    home = stats["chutes_home"]
    away = stats["chutes_away"]

    if home is None or away is None:
        return None

    total = home + away

    if total >= 18:
        return "ALTA"

    if total >= 11:
        return "MODERADA"

    return "BAIXA"


# =========================================================
# DADOS SUFICIENTES?
# =========================================================

def quantidade_dados(stats):

    contador = 0

    for valor in stats.values():

        if valor is not None:
            contador += 1

    return contador


# =========================================================
# DEFINIR OPORTUNIDADE
# =========================================================

def identificar_oportunidade(
    evento,
    stats,
    pressao_home,
    pressao_away
):

    home, away = extrair_times(evento)

    nome_home = nome_time(home)
    nome_away = nome_time(away)

    home_score, away_score = obter_placar(evento)

    sinais = []

    # =====================================================
    # PRESSÃO DE UM TIME
    # =====================================================

    diferenca_pressao = abs(
        pressao_home - pressao_away
    )

    time_pressionando = None
    pressao = 0

    if pressao_home >= 65 and diferenca_pressao >= 20:

        time_pressionando = nome_home
        pressao = pressao_home

    elif pressao_away >= 65 and diferenca_pressao >= 20:

        time_pressionando = nome_away
        pressao = pressao_away

    if time_pressionando:

        sinais.append(
            f"pressão clara do {time_pressionando}"
        )

    # =====================================================
    # ESCANTEIOS
    # =====================================================

    tendencia_corners = tendencia_escanteios(stats)

    if tendencia_corners == "ALTA":

        sinais.append(
            "volume elevado de escanteios"
        )

    # =====================================================
    # FINALIZAÇÕES
    # =====================================================

    tendencia_shots = tendencia_finalizacoes(stats)

    if tendencia_shots == "ALTA":

        sinais.append(
            "volume elevado de finalizações"
        )

    # =====================================================
    # FINALIZAÇÕES NO ALVO
    # =====================================================

    alvo_home = stats["alvo_home"]
    alvo_away = stats["alvo_away"]

    alvo_total = None

    if alvo_home is not None and alvo_away is not None:

        alvo_total = alvo_home + alvo_away

        if alvo_total >= 6:

            sinais.append(
                "muitas finalizações no alvo"
            )

    # =====================================================
    # REGRA DE ENTRADA
    # =====================================================

    entrada = None
    confianca = 0

    # -----------------------------------------------------
    # ESCANTEIOS
    # -----------------------------------------------------

    if (
        tendencia_corners == "ALTA"
        and pressao >= 60
    ):

        entrada = (
            "MERCADO DE ESCANTEIOS — "
            "procurar linha de escanteios do jogo"
        )

        confianca = 72

    # -----------------------------------------------------
    # TIME DOMINANTE
    # -----------------------------------------------------

    elif (
        time_pressionando
        and pressao >= 75
        and alvo_total is not None
        and alvo_total >= 4
    ):

        entrada = (
            f"PROCURAR MERCADO DE {time_pressionando} "
            "— vitória ou próximo gol"
        )

        confianca = 74

    # -----------------------------------------------------
    # GOLS
    # -----------------------------------------------------

    elif (
        tendencia_shots == "ALTA"
        and alvo_total is not None
        and alvo_total >= 6
    ):

        entrada = (
            "MERCADO DE GOLS — "
            "procurar linha de gols ao vivo"
        )

        confianca = 70

    # -----------------------------------------------------
    # SEM ENTRADA
    # -----------------------------------------------------

    else:

        entrada = (
            "SEM ENTRADA — aguardar mais dados"
        )

        confianca = 0

    return {
        "entrada": entrada,
        "confianca": confianca,
        "sinais": sinais,
        "time_pressionando": time_pressionando,
        "pressao": pressao,
    }


# =========================================================
# ANÁLISE COMPLETA
# =========================================================

def analisar_jogo(evento):

    if not esta_live(evento):
        return None

    permitido, liga = liga_permitida(evento)

    if not permitido:
        return None

    eid = event_id(evento)

    if not eid:
        return None

    home, away = extrair_times(evento)

    nome_home = nome_time(home)
    nome_away = nome_time(away)

    home_id = id_time(home)
    away_id = id_time(away)

    estatisticas = obter_estatisticas(eid)

    if not estatisticas:
        return None

    stats = extrair_stats(estatisticas)

    dados = quantidade_dados(stats)

    # Poucos dados = não criar oportunidade
    if dados < 4:
        return None

    pressao_home, pressao_away = calcular_pressao(
        stats
    )

    oportunidade = identificar_oportunidade(
        evento,
        stats,
        pressao_home,
        pressao_away
    )

    return {
        "event_id": eid,
        "liga": liga,
        "home": nome_home,
        "away": nome_away,
        "home_id": home_id,
        "away_id": away_id,
        "stats": stats,
        "pressao_home": pressao_home,
        "pressao_away": pressao_away,
        "oportunidade": oportunidade,
    }


# =========================================================
# FORMATAR ESTATÍSTICAS
# =========================================================

def linha_stat(nome, home, away):

    if home is None or away is None:
        return ""

    return (
        f"• {nome}: "
        f"{home:g} x {away:g}\n"
    )


def formatar_analise(resultado):

    stats = resultado["stats"]

    home = resultado["home"]
    away = resultado["away"]

    evento_fake = {
        "homeTeam": {
            "name": home
        },
        "awayTeam": {
            "name": away
        }
    }

    # Placar não está no resultado, então buscamos novamente
    evento = obter_evento(
        resultado["event_id"]
    )

    if evento:

        home_score, away_score = obter_placar(
            evento
        )

        minuto = obter_minuto(evento)

    else:

        home_score = 0
        away_score = 0
        minuto = ""

    oportunidade = resultado["oportunidade"]

    texto = (

        "🚨 <b>OPORTUNIDADE LIVE</b>\n\n"

        f"🏆 {resultado['liga']}\n"

        f"⚽ <b>{home}</b> x <b>{away}</b>\n"

        f"⏱ {minuto}\n"

        f"📊 Placar: {home_score} x {away_score}\n\n"

        "🔥 <b>PRESSÃO</b>\n"

        f"{home}: {resultado['pressao_home']}/100\n"

        f"{away}: {resultado['pressao_away']}/100\n\n"

        "📊 <b>DADOS LIVE</b>\n"

    )

    texto += linha_stat(
        "Posse",
        stats["posse_home"],
        stats["posse_away"]
    )

    texto += linha_stat(
        "Finalizações",
        stats["chutes_home"],
        stats["chutes_away"]
    )

    texto += linha_stat(
        "No alvo",
        stats["alvo_home"],
        stats["alvo_away"]
    )

    texto += linha_stat(
        "Escanteios",
        stats["escanteios_home"],
        stats["escanteios_away"]
    )

    texto += linha_stat(
        "Ataques perigosos",
        stats["ataques_perigosos_home"],
        stats["ataques_perigosos_away"]
    )

    texto += "\n🎯 <b>ENTRADA SUGERIDA</b>\n"

    texto += (
        f"👉 {oportunidade['entrada']}\n"
    )

    if oportunidade["confianca"] > 0:

        texto += (
            f"\n📈 Força do sinal: "
            f"{oportunidade['confianca']}/100\n"
        )

    else:

        texto += (
            "\n⏳ Ainda não há dados suficientes "
            "para uma entrada clara.\n"
        )

    if oportunidade["sinais"]:

        texto += "\n📌 <b>Motivos:</b>\n"

        for sinal in oportunidade["sinais"]:

            texto += (
                f"• {sinal}\n"
            )

    texto += (
        "\n⚠️ Análise estatística ao vivo. "
        "Não há garantia de resultado."
    )

    return texto


# =========================================================
# FORMATAR LISTA DE JOGOS
# =========================================================

def formatar_jogo(evento):

    permitido, liga = liga_permitida(evento)

    if not permitido:
        return None

    home, away = extrair_times(evento)

    nome_home = nome_time(home)
    nome_away = nome_time(away)

    home_score, away_score = obter_placar(evento)

    status = texto_status(evento)

    eid = event_id(evento)

    return (
        f"⚽ <b>{nome_home}</b> x "
        f"<b>{nome_away}</b>\n"
        f"🏆 {liga}\n"
        f"📊 {home_score} x {away_score}\n"
        f"⏱ {status}\n"
        f"🆔 {eid}"
    )


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,

        "🤖 <b>BOT LIVE ATIVADO</b>\n\n"

        "📡 Fonte: SportAPI7\n\n"

        "O bot monitora somente as competições "
        "configuradas e procura sinais estatísticos "
        "durante as partidas.\n\n"

        "📌 Comandos:\n"
        "/jogos\n"
        "/live\n"
        "/analisar ID\n"
        "/status\n"
        "/parar\n"
        "/continuar\n\n"

        "🎯 O bot somente gera uma entrada quando "
        "os dados disponíveis forem suficientes.\n\n"

        "⚠️ Nenhuma entrada garante resultado.",

        parse_mode="HTML"
    )


# =========================================================
# /STATUS
# =========================================================

@bot.message_handler(commands=["status"])
def status(message):

    estado = (
        "ATIVO 🟢"
        if MONITORANDO
        else
        "PAUSADO 🔴"
    )

    bot.send_message(
        message.chat.id,

        f"🤖 <b>{estado}</b>\n\n"
        f"📡 API: SportAPI7\n"
        f"⏱ Monitoramento: {INTERVALO_LIVE}s\n"
        f"📚 Histórico: {HISTORICO_JOGOS} jogos\n"
        f"🏆 Competições: 11\n"
        f"🎯 EV: desativado",

        parse_mode="HTML"
    )


# =========================================================
# /PARAR
# =========================================================

@bot.message_handler(commands=["parar"])
def parar(message):

    global MONITORANDO

    MONITORANDO = False

    bot.send_message(
        message.chat.id,
        "⏸ Monitoramento pausado."
    )


# =========================================================
# /CONTINUAR
# =========================================================

@bot.message_handler(commands=["continuar"])
def continuar(message):

    global MONITORANDO

    MONITORANDO = True

    bot.send_message(
        message.chat.id,
        "▶️ Monitoramento ativado."
    )


# =========================================================
# /LIVE
# =========================================================

@bot.message_handler(commands=["live"])
def live(message):

    jogos = obter_jogos_live()

    filtrados = []

    for jogo in jogos:

        permitido, _ = liga_permitida(jogo)

        if permitido:
            filtrados.append(jogo)

    if not filtrados:

        bot.send_message(
            message.chat.id,

            "🔴 Nenhum jogo ao vivo das "
            "11 competições configuradas."
        )

        return

    texto = (
        "🔴 <b>JOGOS AO VIVO</b>\n\n"
    )

    for jogo in filtrados[:30]:

        bloco = formatar_jogo(jogo)

        if bloco:
            texto += bloco + "\n\n"

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# /JOGOS
# =========================================================

@bot.message_handler(commands=["jogos"])
def jogos(message):

    live = obter_jogos_live()

    agora = agora_local()

    datas = [
        agora.strftime("%Y-%m-%d"),
        (agora + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        ),
    ]

    todos = {}

    # -----------------------------------------------------
    # AO VIVO
    # -----------------------------------------------------

    for jogo in live:

        permitido, _ = liga_permitida(jogo)

        if not permitido:
            continue

        eid = event_id(jogo)

        if eid:
            todos[str(eid)] = jogo

    # -----------------------------------------------------
    # PRÓXIMOS JOGOS
    # -----------------------------------------------------

    for data in datas:

        encontrados = obter_jogos_data(data)

        for jogo in encontrados:

            permitido, _ = liga_permitida(jogo)

            if not permitido:
                continue

            eid = event_id(jogo)

            if eid:
                todos[str(eid)] = jogo

    lista = list(todos.values())

    if not lista:

        bot.send_message(
            message.chat.id,

            "⚠️ Nenhum jogo das 11 competições "
            "foi encontrado pela SportAPI7 "
            "no período consultado."
        )

        return

    texto = (
        "⚽ <b>JOGOS DAS PRÓXIMAS 24H + LIVE</b>\n\n"
    )

    for jogo in lista[:50]:

        bloco = formatar_jogo(jogo)

        if bloco:
            texto += bloco + "\n\n"

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# /ANALISAR
# =========================================================

@bot.message_handler(commands=["analisar"])
def analisar(message):

    partes = message.text.split()

    if len(partes) < 2:

        bot.send_message(
            message.chat.id,

            "Use:\n"
            "/analisar ID_DO_JOGO\n\n"
            "Você encontra o ID usando /jogos."
        )

        return

    eid = partes[1]

    evento = obter_evento(eid)

    if not evento:

        bot.send_message(
            message.chat.id,
            "❌ Jogo não encontrado."
        )

        return

    permitido, _ = liga_permitida(evento)

    if not permitido:

        bot.send_message(
            message.chat.id,

            "🚫 Esse jogo não pertence "
            "às competições configuradas."
        )

        return

    if not esta_live(evento):

        bot.send_message(
            message.chat.id,

            "ℹ️ Esse jogo ainda não está ao vivo.\n\n"
            "Use /jogos para acompanhar os jogos "
            "das próximas 24 horas."
        )

        return

    resultado = analisar_jogo(evento)

    if not resultado:

        bot.send_message(
            message.chat.id,

            "⏳ Ainda não existem dados estatísticos "
            "suficientes para uma análise segura."
        )

        return

    texto = formatar_analise(resultado)

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# MONITOR LIVE
# =========================================================

def monitorar():

    global MONITORANDO

    while True:

        try:

            if not MONITORANDO:
                time.sleep(INTERVALO_LIVE)
                continue

            if not CHAT_ID:
                time.sleep(INTERVALO_LIVE)
                continue

            jogos = obter_jogos_live()

            for jogo in jogos:

                permitido, _ = liga_permitida(jogo)

                if not permitido:
                    continue

                resultado = analisar_jogo(jogo)

                if not resultado:
                    continue

                oportunidade = resultado[
                    "oportunidade"
                ]

                confianca = oportunidade[
                    "confianca"
                ]

                # Só envia alerta real quando
                # houver uma oportunidade clara.

                if confianca < 70:
                    continue

                eid = resultado["event_id"]

                agora = time.time()

                ultimo = ultimo_alerta.get(
                    eid,
                    0
                )

                if (
                    agora - ultimo
                    < COOLDOWN_ALERTA
                ):
                    continue

                texto = formatar_analise(
                    resultado
                )

                bot.send_message(
                    CHAT_ID,
                    texto,
                    parse_mode="HTML"
                )

                ultimo_alerta[eid] = agora

            time.sleep(INTERVALO_LIVE)

        except Exception as erro:

            print(
                f"ERRO MONITOR LIVE: {erro}"
            )

            time.sleep(30)


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "bot": "SportAPI7",
        "monitoramento": MONITORANDO
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# =========================================================
# TELEGRAM
# =========================================================

def iniciar_bot():

    while True:

        try:

            # Remove eventual webhook anterior.
            bot.remove_webhook()

            time.sleep(2)

            print(
                "Iniciando polling do Telegram..."
            )

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )

        except Exception as erro:

            print(
                f"ERRO TELEGRAM: {erro}"
            )

            time.sleep(15)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("======================================")
    print(" BOT LIVE - SPORTAPI7")
    print("======================================")

    print(
        "Monitoramento:",
        INTERVALO_LIVE,
        "segundos"
    )

    print(
        "Histórico:",
        HISTORICO_JOGOS,
        "jogos"
    )

    print(
        "EV/Kelly: DESATIVADOS"
    )

    # Telegram
    threading.Thread(
        target=iniciar_bot,
        daemon=True
    ).start()

    # Monitoramento
    threading.Thread(
        target=monitorar,
        daemon=True
    ).start()

    porta = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=porta
    )
