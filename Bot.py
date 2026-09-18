import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import telebot
from flask import Flask


BOT_TOKEN = os.getenv("BOT_TOKEN")

API_FOOTBALL_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_SPORTS_KEY")
    or os.getenv("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_FOOTBALL_KEY or ""
}

app = Flask(__name__)

bot = None

BOT_ATIVO = True


# ============================================================
# PRINCIPAIS COMPETIÇÕES
# ============================================================

LIGAS = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    71: "Brasileirão Série A",
    13: "Copa Libertadores",
    11: "Copa Sul-Americana"
}

LIGA_IDS = "-".join(map(str, LIGAS))


# ============================================================
# CONTROLE DE REQUISIÇÕES
# ============================================================

historico_chamadas = []


def pode_fazer_requisicao():

    agora = time.time()

    while historico_chamadas and agora - historico_chamadas[0] > 60:
        historico_chamadas.pop(0)

    if len(historico_chamadas) >= 9:
        return False

    historico_chamadas.append(agora)

    return True


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):

    if not API_FOOTBALL_KEY:
        print("ERRO: API_FOOTBALL_KEY não configurada.")
        return None

    if not pode_fazer_requisicao():
        print("Limite local temporário de chamadas atingido.")
        return None

    try:

        resposta = requests.get(
            API_BASE + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=20
        )

        print(
            f"API {endpoint} | HTTP {resposta.status_code}"
        )

        if resposta.status_code != 200:

            print(
                resposta.text[:500]
            )

            return None

        dados = resposta.json()

        if dados.get("errors"):

            print(
                "API errors:",
                dados["errors"]
            )

        return dados

    except Exception as e:

        print(
            "API error:",
            e
        )

        return None


# ============================================================
# STATUS DA API
# ============================================================

def verificar_api():

    dados = api_get("/status")

    if not dados:

        return False, "Sem resposta da API."

    if dados.get("errors"):

        return False, str(
            dados["errors"]
        )

    response = dados.get(
        "response",
        {}
    )

    account = response.get(
        "account",
        {}
    )

    subscription = response.get(
        "subscription",
        {}
    )

    requests_data = response.get(
        "requests",
        {}
    )

    status = account.get(
        "status",
        "unknown"
    )

    plano = subscription.get(
        "plan",
        "unknown"
    )

    usados = requests_data.get(
        "current",
        0
    )

    limite = requests_data.get(
        "limit_day",
        0
    )

    mensagem = (
        f"Conta: {status}\n"
        f"Plano: {plano}\n"
        f"Requisições hoje: "
        f"{usados}/{limite}"
    )

    return status == "active", mensagem


# ============================================================
# JOGOS DO DIA
# ============================================================

def jogos_do_dia(data):

    resultados = []

    for liga_id, nome_liga in LIGAS.items():

        dados = api_get(
            "/fixtures",
            {
                "date": data,
                "league": liga_id,
                "timezone": "America/Sao_Paulo"
            }
        )

        if not dados:
            continue

        jogos = dados.get(
            "response",
            []
        )

        for jogo in jogos:

            jogo["_nome_liga_bot"] = nome_liga

            resultados.append(
                jogo
            )

        time.sleep(0.5)

    return resultados


# ============================================================
# ÚLTIMOS 10 JOGOS
# ============================================================

def ultimos_10(team_id):

    if not team_id:
        return []

    dados = api_get(
        "/fixtures",
        {
            "team": team_id,
            "last": 10
        }
    )

    if not dados:
        return []

    if dados.get("errors"):

        print(
            f"Erro histórico {team_id}:",
            dados["errors"]
        )

        return []

    jogos = dados.get(
        "response",
        []
    )

    finalizados = []

    for jogo in jogos:

        status = (
            jogo
            .get("fixture", {})
            .get("status", {})
            .get("short")
        )

        if status in (
            "FT",
            "AET",
            "PEN"
        ):

            finalizados.append(
                jogo
            )

    print(
        f"Time {team_id}: "
        f"{len(finalizados)} jogos encontrados."
    )

    return finalizados[:10]


# ============================================================
# RESUMO
# ============================================================

def resumo(jogos, team_id):

    if not jogos:

        return {
            "disponivel": False
        }

    vitorias = 0
    empates = 0
    derrotas = 0

    gols_marcados = 0
    gols_sofridos = 0

    over15 = 0
    over25 = 0
    ambas = 0

    total = 0

    for jogo in jogos:

        home = (
            jogo
            .get("teams", {})
            .get("home", {})
        )

        away = (
            jogo
            .get("teams", {})
            .get("away", {})
        )

        gols = jogo.get(
            "goals",
            {}
        )

        gh = gols.get("home")
        ga = gols.get("away")

        if gh is None or ga is None:
            continue

        if home.get("id") == team_id:

            marcados = gh
            sofridos = ga

        elif away.get("id") == team_id:

            marcados = ga
            sofridos = gh

        else:

            continue

        total += 1

        gols_marcados += marcados
        gols_sofridos += sofridos

        if marcados > sofridos:

            vitorias += 1

        elif marcados == sofridos:

            empates += 1

        else:

            derrotas += 1

        total_gols = (
            marcados +
            sofridos
        )

        if total_gols >= 2:

            over15 += 1

        if total_gols >= 3:

            over25 += 1

        if (
            marcados > 0
            and sofridos > 0
        ):

            ambas += 1

    if total == 0:

        return {
            "disponivel": False
        }

    return {
        "disponivel": True,
        "jogos": total,
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "gols_marcados": gols_marcados,
        "gols_sofridos": gols_sofridos,
        "over15": round(
            over15 / total * 100
        ),
        "over25": round(
            over25 / total * 100
        ),
        "ambas": round(
            ambas / total * 100
        )
    }


# ============================================================
# TEXTO DO RESUMO
# ============================================================

def texto_resumo(
    nome,
    dados,
    emoji
):

    if not dados.get(
        "disponivel"
    ):

        return (
            f"{emoji} **{nome}**\n"
            "⚠️ Histórico recente "
            "indisponível.\n"
        )

    return (
        f"{emoji} **{nome}**\n"
        f"📊 Jogos analisados: "
        f"{dados['jogos']}\n"
        f"🏆 {dados['vitorias']}V "
        f"{dados['empates']}E "
        f"{dados['derrotas']}D\n"
        f"⚽ Gols: "
        f"{dados['gols_marcados']} "
        f"marcados / "
        f"{dados['gols_sofridos']} sofridos\n"
        f"📈 Over 1,5: "
        f"{dados['over15']}%\n"
        f"📈 Over 2,5: "
        f"{dados['over25']}%\n"
        f"🎯 Ambas marcam: "
        f"{dados['ambas']}%\n"
    )


# ============================================================
# ANÁLISE PRÉ-JOGO
# ============================================================

def analisar_jogo(jogo):

    home = (
        jogo
        .get("teams", {})
        .get("home", {})
    )

    away = (
        jogo
        .get("teams", {})
        .get("away", {})
    )

    home_id = home.get("id")
    away_id = away.get("id")

    home_jogos = ultimos_10(
        home_id
    )

    time.sleep(1)

    away_jogos = ultimos_10(
        away_id
    )

    home_resumo = resumo(
        home_jogos,
        home_id
    )

    away_resumo = resumo(
        away_jogos,
        away_id
    )

    oportunidades = []

    if (
        home_resumo.get("disponivel")
        and away_resumo.get("disponivel")
    ):

        home_forca = (
            home_resumo["vitorias"] * 3
            + home_resumo["empates"]
        )

        away_forca = (
            away_resumo["vitorias"] * 3
            + away_resumo["empates"]
        )

        if home_forca >= away_forca:

            oportunidades.append({
                "titulo":
                    f"{home.get('name')} "
                    "ou empate (1X)",

                "mercado":
                    "Dupla possibilidade",

                "forca":
                    home_forca -
                    away_forca,

                "motivo":
                    "melhor desempenho "
                    "recente do mandante"
            })

        else:

            oportunidades.append({
                "titulo":
                    f"Empate ou "
                    f"{away.get('name')} (X2)",

                "mercado":
                    "Dupla possibilidade",

                "forca":
                    away_forca -
                    home_forca,

                "motivo":
                    "melhor desempenho "
                    "recente do visitante"
            })

        over25 = (
            home_resumo["over25"]
            + away_resumo["over25"]
        ) / 2

        over15 = (
            home_resumo["over15"]
            + away_resumo["over15"]
        ) / 2

        ambas = (
            home_resumo["ambas"]
            + away_resumo["ambas"]
        ) / 2

        if over25 >= 60:

            oportunidades.append({
                "titulo":
                    "Mais de 2,5 gols",

                "mercado":
                    "Mais/Menos gols",

                "forca":
                    over25,

                "motivo":
                    f"Over 2,5 em média "
                    f"de {round(over25)}% "
                    "dos últimos jogos"
            })

        elif over15 >= 75:

            oportunidades.append({
                "titulo":
                    "Mais de 1,5 gols",

                "mercado":
                    "Mais/Menos gols",

                "forca":
                    over15,

                "motivo":
                    f"Over 1,5 em média "
                    f"de {round(over15)}% "
                    "dos últimos jogos"
            })

        elif ambas >= 60:

            oportunidades.append({
                "titulo":
                    "Ambas marcam",

                "mercado":
                    "Ambas Marcam",

                "forca":
                    ambas,

                "motivo":
                    f"Ambas marcam em média "
                    f"de {round(ambas)}% "
                    "dos últimos jogos"
            })

    if not oportunidades:

        oportunidades.append({
            "titulo":
                "Aguardar mais dados",

            "mercado":
                "Sem entrada estatística",

            "forca":
                0,

            "motivo":
                "Não há histórico "
                "suficiente para gerar "
                "uma oportunidade."
        })

    oportunidades.sort(
        key=lambda x: x["forca"],
        reverse=True
    )

    return (
        home_resumo,
        away_resumo,
        oportunidades[:2]
    )


# ============================================================
# FORMATA JOGO
# ============================================================

def formatar_jogo(jogo):

    fixture = jogo.get(
        "fixture",
        {}
    )

    home = (
        jogo
        .get("teams", {})
        .get("home", {})
    )

    away = (
        jogo
        .get("teams", {})
        .get("away", {})
    )

    data_hora = fixture.get(
        "date",
        ""
    )

    if "T" in data_hora:

        data, hora = (
            data_hora.split("T")[0],
            data_hora.split("T")[1][:5]
        )

        partes = data.split("-")

        data = (
            f"{partes[2]}/"
            f"{partes[1]}/"
            f"{partes[0]}"
        )

    else:

        data = data_hora
        hora = ""

    home_resumo, away_resumo, oportunidades = (
        analisar_jogo(jogo)
    )

    liga = (
        jogo.get("_nome_liga_bot")
        or jogo
        .get("league", {})
        .get("name", "Competição")
    )

    texto = (
        f"⚽ **{home.get('name')} "
        f"x {away.get('name')}**\n"
        f"🏆 {liga}\n"
        f"🕐 {data} {hora}\n\n"
        "📊 **ÚLTIMAS 10 PARTIDAS**\n\n"
    )

    texto += texto_resumo(
        home.get("name"),
        home_resumo,
        "🏠"
    )

    texto += "\n"

    texto += texto_resumo(
        away.get("name"),
        away_resumo,
        "✈️"
    )

    texto += (
        "\n🎯 **2 MELHORES "
        "OPORTUNIDADES**\n\n"
    )

    for i, op in enumerate(
        oportunidades,
        1
    ):

        texto += (
            f"**{i}. {op['titulo']}**\n"
            f"📌 Mercado: "
            f"{op['mercado']}\n"
            f"📊 Indicador estatístico: "
            f"**{round(op['forca'])}**\n"
            f"• {op['motivo']}\n\n"
        )

    texto += (
        "⚠️ Análise estatística "
        "informativa. "
        "Não garante resultado."
    )

    return texto


# ============================================================
# JOGOS AO VIVO
# SOMENTE PRINCIPAIS LIGAS
# ============================================================

def jogos_live():

    dados = api_get(
        "/fixtures",
        {
            "live": LIGA_IDS,
            "timezone":
                "America/Sao_Paulo"
        }
    )

    if not dados:
        return []

    if dados.get("errors"):
        return []

    jogos = []

    for jogo in dados.get(
        "response",
        []
    ):

        league_id = (
            jogo
            .get("league", {})
            .get("id")
        )

        if league_id in LIGAS:

            jogo["_nome_liga_bot"] = (
                LIGAS[league_id]
            )

            jogos.append(
                jogo
            )

    return jogos


# ============================================================
# ESTATÍSTICAS LIVE
# ============================================================

def estatisticas_live(
    fixture_id
):

    dados = api_get(
        "/fixtures/statistics",
        {
            "fixture": fixture_id
        }
    )

    if not dados:
        return None

    if dados.get("errors"):
        return None

    return dados.get(
        "response",
        []
    )


# ============================================================
# PEGA ESTATÍSTICA
# ============================================================

def stat(
    lista,
    nome
):

    for item in lista or []:

        if item.get("type") != nome:
            continue

        valor = item.get(
            "value"
        )

        if valor is None:

            return None

        try:

            return float(
                str(valor)
                .replace("%", "")
                .replace(",", ".")
            )

        except:

            return None

    return None


def fmt(valor):

    if valor is None:
        return "—"

    if float(valor).is_integer():
        return str(
            int(valor)
        )

    return f"{valor:.1f}"


# ============================================================
# ANÁLISE AO VIVO
# ============================================================

def analisar_live(jogo):

    fixture = jogo.get(
        "fixture",
        {}
    )

    home = (
        jogo
        .get("teams", {})
        .get("home", {})
    )

    away = (
        jogo
        .get("teams", {})
        .get("away", {})
    )

    gols_home = (
        jogo
        .get("goals", {})
        .get("home", 0)
        or 0
    )

    gols_away = (
        jogo
        .get("goals", {})
        .get("away", 0)
        or 0
    )

    minuto = (
        fixture
        .get("status", {})
        .get("elapsed", "?")
    )

    stats = estatisticas_live(
        fixture.get("id")
    )

    # ========================================================
    # SEM ESTATÍSTICAS
    # ========================================================

    if stats is None:

        return (
            "🔴 **JOGO AO VIVO**\n\n"

            f"⚽ **{home.get('name')} "
            f"{gols_home} x "
            f"{gols_away} "
            f"{away.get('name')}**\n"

            f"⏱️ Minuto: {minuto}'\n\n"

            "📊 **ESTATÍSTICAS**\n"

            "⚠️ Estatísticas da partida "
            "ainda não disponíveis na API.\n\n"

            "ℹ️ Ausência de dados "
            "não será interpretada "
            "como zero."
        )

    home_stats = []
    away_stats = []

    for equipe in stats:

        team_id = (
            equipe
            .get("team", {})
            .get("id")
        )

        if team_id == home.get("id"):

            home_stats = equipe.get(
                "statistics",
                []
            )

        elif team_id == away.get("id"):

            away_stats = equipe.get(
                "statistics",
                []
            )

    home_posse = stat(
        home_stats,
        "Ball Possession"
    )

    away_posse = stat(
        away_stats,
        "Ball Possession"
    )

    home_shots = stat(
        home_stats,
        "Total Shots"
    )

    away_shots = stat(
        away_stats,
        "Total Shots"
    )

    home_on = stat(
        home_stats,
        "Shots on Goal"
    )

    away_on = stat(
        away_stats,
        "Shots on Goal"
    )

    home_corners = stat(
        home_stats,
        "Corner Kicks"
    )

    away_corners = stat(
        away_stats,
        "Corner Kicks"
    )

    home_dangerous = stat(
        home_stats,
        "Dangerous Attacks"
    )

    away_dangerous = stat(
        away_stats,
        "Dangerous Attacks"
    )

    texto = (
        "🔴 **JOGO AO VIVO**\n\n"

        f"⚽ **{home.get('name')} "
        f"{gols_home} x "
        f"{gols_away} "
        f"{away.get('name')}**\n"

        f"⏱️ Minuto: {minuto}'\n\n"

        "📊 **ESTATÍSTICAS**\n\n"

        f"🏠 {home.get('name')}\n"
        f"• Posse: {fmt(home_posse)}%\n"
        f"• Chutes: {fmt(home_shots)}\n"
        f"• Chutes no gol: {fmt(home_on)}\n"
        f"• Escanteios: {fmt(home_corners)}\n"
        f"• Ataques perigosos: "
        f"{fmt(home_dangerous)}\n\n"

        f"✈️ {away.get('name')}\n"
        f"• Posse: {fmt(away_posse)}%\n"
        f"• Chutes: {fmt(away_shots)}\n"
        f"• Chutes no gol: {fmt(away_on)}\n"
        f"• Escanteios: {fmt(away_corners)}\n"
        f"• Ataques perigosos: "
        f"{fmt(away_dangerous)}\n\n"
    )

    # ========================================================
    # PRESSÃO
    # ========================================================

    dados_pressao = [
        home_posse,
        away_posse,
        home_shots,
        away_shots,
        home_on,
        away_on,
        home_corners,
        away_corners
    ]

    if all(
        valor is not None
        for valor in dados_pressao
    ):

        pressao_home = (
            home_posse * 0.20
            + home_shots
            + home_on * 1.5
            + home_corners * 1.2
        )

        pressao_away = (
            away_posse * 0.20
            + away_shots
            + away_on * 1.5
            + away_corners * 1.2
        )

        if (
            pressao_home
            > pressao_away * 1.20
        ):

            texto += (
                f"🔥 **Maior pressão: "
                f"{home.get('name')}**\n"
            )

        elif (
            pressao_away
            > pressao_home * 1.20
        ):

            texto += (
                f"🔥 **Maior pressão: "
                f"{away.get('name')}**\n"
            )

        else:

            texto += (
                "⚖️ **Pressão equilibrada**\n"
            )

    else:

        texto += (
            "⚠️ Pressão não calculada: "
            "faltam estatísticas da API.\n"
        )

    if (
        home_corners is not None
        and away_corners is not None
        and home_corners + away_corners >= 7
    ):

        texto += (
            "🎯 **Indicador:** "
            "volume de escanteios elevado.\n"
        )

    if (
        home_shots is not None
        and away_shots is not None
        and home_shots + away_shots >= 12
    ):

        texto += (
            "⚽ **Indicador:** "
            "volume de finalizações elevado.\n"
        )

    texto += (
        "\n⚠️ Análise estatística "
        "informativa. "
        "Não garante resultado."
    )

    return texto


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    global bot

    if not BOT_TOKEN:
        return

    bot = telebot.TeleBot(
        BOT_TOKEN,
        parse_mode="Markdown"
    )


    @bot.message_handler(
        commands=["start"]
    )
    def start(message):

        bot.send_message(
            message.chat.id,

            "🤖 **BOT DE ANÁLISE DE FUTEBOL**\n\n"

            "🟢 Bot conectado.\n\n"

            "📡 Fonte: "
            "API-Sports / API-Football\n\n"

            "⚽ /jogos — Jogos do dia\n"
            "🔴 /live — Jogos ao vivo "
            "das principais ligas\n"
            "🔎 /analisar ID — "
            "Analisar um jogo\n"
            "📡 /status — Ver conexão\n"
            "⏸️ /pausar — Pausar bot\n"
            "▶️ /ativar — Ativar bot\n\n"

            "ℹ️ As análises são "
            "informativas e não "
            "garantem resultados."
        )


    @bot.message_handler(
        commands=["status"]
    )
    def status(message):

        ok, info = verificar_api()

        if ok:

            texto = (
                "🟢 **API ONLINE**\n\n"
                + info
            )

        else:

            texto = (
                "🔴 **API OFFLINE**\n\n"
                + info
            )

        bot.send_message(
            message.chat.id,
            texto
        )


    @bot.message_handler(
        commands=["pausar"]
    )
    def pausar(message):

        global BOT_ATIVO

        BOT_ATIVO = False

        bot.send_message(
            message.chat.id,
            "⏸️ Bot pausado."
        )


    @bot.message_handler(
        commands=["ativar"]
    )
    def ativar(message):

        global BOT_ATIVO

        BOT_ATIVO = True

        bot.send_message(
            message.chat.id,
            "▶️ Bot ativado."
        )


    @bot.message_handler(
        commands=["jogos"]
    )
    def jogos(message):

        if not BOT_ATIVO:

            bot.send_message(
                message.chat.id,
                "⏸️ Bot pausado."
            )

            return

        bot.send_message(
            message.chat.id,
            "🔎 **Buscando jogos "
            "das principais ligas...**"
        )

        try:

            data = (
                datetime
                .now(
                    ZoneInfo(
                        "America/Sao_Paulo"
                    )
                )
                .strftime("%Y-%m-%d")
            )

            lista = jogos_do_dia(
                data
            )

            if not lista:

                bot.send_message(
                    message.chat.id,
                    "⚽ Nenhum jogo encontrado "
                    "nas principais competições "
                    "para hoje."
                )

                return

            for jogo in lista[:20]:

                bot.send_message(
                    message.chat.id,
                    formatar_jogo(jogo)
                )

                time.sleep(1)

        except Exception as e:

            print(
                "Erro /jogos:",
                e
            )

            bot.send_message(
                message.chat.id,
                "❌ Erro ao buscar "
                "os jogos."
            )


    @bot.message_handler(
        commands=["live"]
    )
    def live(message):

        if not BOT_ATIVO:

            bot.send_message(
                message.chat.id,
                "⏸️ Bot pausado."
            )

            return

        bot.send_message(
            message.chat.id,
            "🔴 **Consultando jogos ao vivo "
            "das principais ligas...**"
        )

        try:

            lista = jogos_live()

            if not lista:

                bot.send_message(
                    message.chat.id,
                    "⚽ Nenhum jogo ao vivo "
                    "encontrado nas principais ligas."
                )

                return

            for jogo in lista[:10]:

                bot.send_message(
                    message.chat.id,
                    analisar_live(jogo)
                )

                time.sleep(1)

        except Exception as e:

            print(
                "Erro /live:",
                e
            )

            bot.send_message(
                message.chat.id,
                "❌ Erro ao consultar "
                "jogos ao vivo."
            )


    @bot.message_handler(
        commands=["analisar"]
    )
    def analisar(message):

        partes = message.text.split()

        if len(partes) < 2:

            bot.send_message(
                message.chat.id,
                "Use /analisar ID"
            )

            return

        try:

            fixture_id = int(
                partes[1]
            )

        except:

            bot.send_message(
                message.chat.id,
                "❌ ID inválido."
            )

            return

        dados = api_get(
            "/fixtures",
            {
                "id": fixture_id,
                "timezone":
                    "America/Sao_Paulo"
            }
        )

        if (
            not dados
            or not dados.get("response")
        ):

            bot.send_message(
                message.chat.id,
                "❌ Partida não encontrada."
            )

            return

        jogo = dados[
            "response"
        ][0]

        league_id = (
            jogo
            .get("league", {})
            .get("id")
        )

        if league_id not in LIGAS:

            bot.send_message(
                message.chat.id,
                "⚠️ Esta partida não "
                "pertence às principais "
                "competições configuradas."
            )

            return

        jogo["_nome_liga_bot"] = (
            LIGAS[league_id]
        )

        bot.send_message(
            message.chat.id,
            formatar_jogo(jogo)
        )


    while True:

        try:

            bot.remove_webhook()

            time.sleep(1)

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                "Telegram:",
                e
            )

            time.sleep(10)


# ============================================================
# RENDER
# ============================================================

@app.route("/")
def home():

    return "Bot funcionando!"


@app.route("/health")
def health():

    return {
        "status": "online",
        "bot": "telegram",
        "api":
            "API-Sports / API-Football",
        "ligas":
            list(LIGAS.values())
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print(
        "BOT DE FUTEBOL | "
        "API-Sports / API-Football"
    )

    if (
        BOT_TOKEN
        and API_FOOTBALL_KEY
    ):

        print(
            "Variáveis encontradas."
        )

    else:

        print(
            "ERRO: variáveis ausentes."
        )

    threading.Thread(
        target=iniciar_bot,
        daemon=True
    ).start()

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
