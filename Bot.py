import os
import time
import threading
import requests
import telebot

from flask import Flask


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

API_FOOTBALL_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_SPORTS_KEY")
    or os.getenv("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_FOOTBALL_KEY
}

app = Flask(__name__)

bot = None

BOT_ATIVO = True


# ============================================================
# LIGAS
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


# ============================================================
# CONTROLE SIMPLES DE REQUISIÇÕES
# ============================================================

historico_chamadas = []


def pode_fazer_requisicao():
    agora = time.time()

    # Remove chamadas com mais de 60 segundos
    while historico_chamadas:
        if agora - historico_chamadas[0] > 60:
            historico_chamadas.pop(0)
        else:
            break

    # Mantém o limite abaixo de 10/minuto
    if len(historico_chamadas) >= 9:
        return False

    historico_chamadas.append(agora)

    return True


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):

    if not API_FOOTBALL_KEY:
        print("❌ API_FOOTBALL_KEY não configurada.")
        return None

    if not pode_fazer_requisicao():
        print("⏳ Limite temporário de chamadas atingido.")
        return None

    try:

        url = API_BASE + endpoint

        resposta = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=20
        )

        print(
            f"API {endpoint} | "
            f"HTTP {resposta.status_code}"
        )

        if resposta.status_code == 429:
            print("⏳ API retornou 429.")
            return None

        if resposta.status_code != 200:
            print(
                f"❌ Erro HTTP {resposta.status_code}: "
                f"{resposta.text[:500]}"
            )
            return None

        dados = resposta.json()

        if dados.get("errors"):
            print(
                f"⚠️ Erros da API: "
                f"{dados.get('errors')}"
            )

        return dados

    except Exception as e:

        print(
            f"❌ Erro na comunicação com API: {e}"
        )

        return None


# ============================================================
# STATUS DA API
# ============================================================

def verificar_api():

    try:

        dados = api_get("/status")

        if not dados:
            return False, "Sem resposta da API."

        if dados.get("errors"):
            return False, str(dados["errors"])

        response = dados.get("response", {})

        account = response.get("account", {})
        subscription = response.get("subscription", {})
        requests_data = response.get("requests", {})

        status = account.get("status", "unknown")
        plano = subscription.get("plan", "unknown")

        usados = requests_data.get("current", 0)
        limite = requests_data.get("limit_day", 0)

        mensagem = (
            f"Conta: {status}\n"
            f"Plano: {plano}\n"
            f"Requisições hoje: {usados}/{limite}"
        )

        if status != "active":
            return False, mensagem

        return True, mensagem

    except Exception as e:

        return False, str(e)


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

        jogos = dados.get("response", [])

        for jogo in jogos:

            jogo["_nome_liga_bot"] = nome_liga

            resultados.append(jogo)

            # Pequeno intervalo para evitar várias chamadas seguidas
            time.sleep(0.5)

    return resultados


# ============================================================
# ÚLTIMOS 10 JOGOS
# ============================================================
#
# CORREÇÃO PRINCIPAL:
#
# Antes:
# team + league + season + last=10
#
# Agora:
# team + last=10
#
# Assim buscamos os 10 jogos mais recentes da equipe,
# independentemente da competição.
# ============================================================

def ultimos_10(team_id):

    try:

        dados = api_get(
            "/fixtures",
            {
                "team": team_id,
                "last": 10
            }
        )

        if not dados:

            print(
                f"⚠️ Sem resposta ao buscar "
                f"últimos jogos do time {team_id}"
            )

            return []

        if dados.get("errors"):

            print(
                f"⚠️ Erro histórico do time "
                f"{team_id}: {dados['errors']}"
            )

            return []

        jogos = dados.get("response", [])

        if not isinstance(jogos, list):

            return []

        finalizados = []

        for jogo in jogos:

            fixture = jogo.get("fixture", {})
            status = fixture.get("status", {})

            codigo = status.get("short")

            # Partidas realmente encerradas
            if codigo in ["FT", "AET", "PEN"]:

                finalizados.append(jogo)

        print(
            f"📊 Time {team_id}: "
            f"{len(finalizados)} jogos encontrados"
        )

        return finalizados[:10]

    except Exception as e:

        print(
            f"❌ Erro ao buscar histórico "
            f"do time {team_id}: {e}"
        )

        return []


# ============================================================
# RESUMO DAS ÚLTIMAS 10 PARTIDAS
# ============================================================

def resumo(jogos, team_id):

    if not jogos:

        return {
            "disponivel": False,
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "gols_marcados": 0,
            "gols_sofridos": 0,
            "over15": 0,
            "over25": 0,
            "ambas": 0
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

        teams = jogo.get("teams", {})
        gols = jogo.get("goals", {})

        home = teams.get("home", {})
        away = teams.get("away", {})

        home_id = home.get("id")
        away_id = away.get("id")

        gh = gols.get("home")
        ga = gols.get("away")

        if gh is None or ga is None:
            continue

        if home_id == team_id:

            marcados = gh
            sofridos = ga

        elif away_id == team_id:

            marcados = ga
            sofridos = gh

        else:

            continue

        total += 1

        gols_marcados += marcados
        gols_sofridos += sofridos

        # Resultado
        if marcados > sofridos:

            vitorias += 1

        elif marcados == sofridos:

            empates += 1

        else:

            derrotas += 1

        # Total de gols
        total_gols = marcados + sofridos

        if total_gols >= 2:

            over15 += 1

        if total_gols >= 3:

            over25 += 1

        if marcados > 0 and sofridos > 0:

            ambas += 1

    # Se nenhuma partida pôde ser processada,
    # NÃO fingimos que foram 0V 0E 0D.
    if total == 0:

        return {
            "disponivel": False,
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "gols_marcados": 0,
            "gols_sofridos": 0,
            "over15": 0,
            "over25": 0,
            "ambas": 0
        }

    return {
        "disponivel": True,
        "jogos": total,
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "gols_marcados": gols_marcados,
        "gols_sofridos": gols_sofridos,
        "over15": round((over15 / total) * 100),
        "over25": round((over25 / total) * 100),
        "ambas": round((ambas / total) * 100)
    }


# ============================================================
# FORMATAÇÃO DO RESUMO
# ============================================================

def texto_resumo(nome, dados, emoji):

    texto = f"{emoji} **{nome}**\n"

    if not dados["disponivel"]:

        texto += (
            "⚠️ Histórico recente indisponível.\n"
        )

        return texto

    texto += (
        f"📊 Jogos analisados: {dados['jogos']}\n"
        f"🏆 {dados['vitorias']}V "
        f"{dados['empates']}E "
        f"{dados['derrotas']}D\n"
        f"⚽ Gols: {dados['gols_marcados']} "
        f"marcados / "
        f"{dados['gols_sofridos']} sofridos\n"
        f"📈 Over 1,5: {dados['over15']}%\n"
        f"📈 Over 2,5: {dados['over25']}%\n"
        f"🎯 Ambas marcam: {dados['ambas']}%\n"
    )

    return texto


# ============================================================
# ANÁLISE PRÉ-JOGO
# ============================================================

def analisar_jogo(jogo):

    teams = jogo.get("teams", {})

    home = teams.get("home", {})
    away = teams.get("away", {})

    home_id = home.get("id")
    away_id = away.get("id")

    home_name = home.get("name", "Mandante")
    away_name = away.get("name", "Visitante")

    home_jogos = ultimos_10(home_id)

    # Pequena pausa para não disparar as duas consultas
    time.sleep(1)

    away_jogos = ultimos_10(away_id)

    home_resumo = resumo(
        home_jogos,
        home_id
    )

    away_resumo = resumo(
        away_jogos,
        away_id
    )

    oportunidades = []

    # ========================================================
    # OPORTUNIDADE 1 - RESULTADO / DUPLA POSSIBILIDADE
    # ========================================================

    if (
        home_resumo["disponivel"]
        and away_resumo["disponivel"]
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
                "titulo": f"{home_name} ou empate (1X)",
                "mercado": "Dupla possibilidade",
                "forca": home_forca - away_forca,
                "motivo": (
                    "melhor desempenho recente "
                    "do mandante"
                )
            })

        else:

            oportunidades.append({
                "titulo": f"Empate ou {away_name} (X2)",
                "mercado": "Dupla possibilidade",
                "forca": away_forca - home_forca,
                "motivo": (
                    "melhor desempenho recente "
                    "do visitante"
                )
            })

    # ========================================================
    # OPORTUNIDADE 2 - GOLS
    # ========================================================

    if (
        home_resumo["disponivel"]
        and away_resumo["disponivel"]
    ):

        over25_media = (
            home_resumo["over25"]
            + away_resumo["over25"]
        ) / 2

        over15_media = (
            home_resumo["over15"]
            + away_resumo["over15"]
        ) / 2

        ambas_media = (
            home_resumo["ambas"]
            + away_resumo["ambas"]
        ) / 2

        if over25_media >= 60:

            oportunidades.append({
                "titulo": "Mais de 2,5 gols",
                "mercado": "Mais/Menos gols",
                "forca": over25_media,
                "motivo": (
                    f"Over 2,5 em média de "
                    f"{round(over25_media)}% "
                    f"dos últimos jogos"
                )
            })

        elif over15_media >= 75:

            oportunidades.append({
                "titulo": "Mais de 1,5 gols",
                "mercado": "Mais/Menos gols",
                "forca": over15_media,
                "motivo": (
                    f"Over 1,5 em média de "
                    f"{round(over15_media)}% "
                    f"dos últimos jogos"
                )
            })

        elif ambas_media >= 60:

            oportunidades.append({
                "titulo": "Ambas marcam",
                "mercado": "Ambas Marcam",
                "forca": ambas_media,
                "motivo": (
                    f"Ambas marcam em média de "
                    f"{round(ambas_media)}% "
                    f"dos últimos jogos"
                )
            })

    # ========================================================
    # SE NÃO HOUVER HISTÓRICO
    # ========================================================

    if not oportunidades:

        oportunidades.append({
            "titulo": "Aguardar mais dados",
            "mercado": "Sem entrada estatística",
            "forca": 0,
            "motivo": (
                "Não há histórico suficiente "
                "para gerar uma oportunidade."
            )
        })

    # Ordena pelas melhores indicações
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

    fixture = jogo.get("fixture", {})

    teams = jogo.get("teams", {})

    home = teams.get("home", {})
    away = teams.get("away", {})

    home_name = home.get("name", "Mandante")
    away_name = away.get("name", "Visitante")

    data_hora = fixture.get("date", "")

    if "T" in data_hora:

        data = data_hora.split("T")[0]
        hora = data_hora.split("T")[1][:5]

        partes = data.split("-")

        if len(partes) == 3:

            data_formatada = (
                f"{partes[2]}/"
                f"{partes[1]}/"
                f"{partes[0]}"
            )

        else:

            data_formatada = data

    else:

        data_formatada = data_hora
        hora = ""

    liga = jogo.get(
        "_nome_liga_bot",
        jogo.get("league", {}).get(
            "name",
            "Competição"
        )
    )

    (
        home_resumo,
        away_resumo,
        oportunidades
    ) = analisar_jogo(jogo)

    texto = (
        f"⚽ **{home_name} x {away_name}**\n"
        f"🏆 {liga}\n"
        f"🕐 {data_formatada} {hora}\n\n"
    )

    texto += "📊 **ÚLTIMAS 10 PARTIDAS**\n\n"

    texto += texto_resumo(
        home_name,
        home_resumo,
        "🏠"
    )

    texto += "\n"

    texto += texto_resumo(
        away_name,
        away_resumo,
        "✈️"
    )

    texto += "\n🎯 **2 MELHORES OPORTUNIDADES**\n\n"

    for i, oportunidade in enumerate(
        oportunidades,
        start=1
    ):

        texto += (
            f"**{i}. "
            f"{oportunidade['titulo']}**\n"
            f"📌 Mercado: "
            f"{oportunidade['mercado']}\n"
            f"📊 Indicador estatístico: "
            f"**{round(oportunidade['forca'])}**\n"
            f"• {oportunidade['motivo']}\n\n"
        )

    texto += (
        "⚠️ Análise estatística informativa. "
        "Não garante resultado."
    )

    return texto


# ============================================================
# JOGOS AO VIVO
# ============================================================

def jogos_live():

    dados = api_get(
        "/fixtures",
        {
            "live": "all",
            "timezone": "America/Sao_Paulo"
        }
    )

    if not dados:

        return []

    if dados.get("errors"):

        print(
            f"⚠️ Erro nos jogos ao vivo: "
            f"{dados['errors']}"
        )

        return []

    return dados.get("response", [])


# ============================================================
# ESTATÍSTICAS AO VIVO
# ============================================================

def estatisticas_live(fixture_id):

    dados = api_get(
        "/fixtures/statistics",
        {
            "fixture": fixture_id
        }
    )

    if not dados:

        return []

    if dados.get("errors"):

        print(
            f"⚠️ Erro nas estatísticas live: "
            f"{dados['errors']}"
        )

        return []

    return dados.get("response", [])


# ============================================================
# EXTRAI ESTATÍSTICA
# ============================================================

def pegar_statisticas_time(lista, nome):

    for item in lista:

        if item.get("type") == nome:

            valor = item.get("value")

            if valor is None:
                return 0

            if isinstance(valor, str):

                valor = (
                    valor
                    .replace("%", "")
                    .replace(",", ".")
                )

            try:
                return float(valor)

            except:
                return 0

    return 0


# ============================================================
# ANÁLISE LIVE
# ============================================================

def analisar_live(jogo):

    fixture = jogo.get("fixture", {})

    teams = jogo.get("teams", {})

    home = teams.get("home", {})
    away = teams.get("away", {})

    home_name = home.get("name", "Mandante")
    away_name = away.get("name", "Visitante")

    home_id = home.get("id")
    away_id = away.get("id")

    fixture_id = fixture.get("id")

    gols = jogo.get("goals", {})

    gols_home = gols.get("home", 0) or 0
    gols_away = gols.get("away", 0) or 0

    elapsed = (
        fixture
        .get("status", {})
        .get("elapsed", "?")
    )

    stats = estatisticas_live(
        fixture_id
    )

    home_stats = []
    away_stats = []

    for equipe in stats:

        team = equipe.get("team", {})

        if team.get("id") == home_id:

            home_stats = equipe.get(
                "statistics",
                []
            )

        elif team.get("id") == away_id:

            away_stats = equipe.get(
                "statistics",
                []
            )

    home_posse = pegar_statisticas_time(
        home_stats,
        "Ball Possession"
    )

    away_posse = pegar_statisticas_time(
        away_stats,
        "Ball Possession"
    )

    home_shots = pegar_statisticas_time(
        home_stats,
        "Total Shots"
    )

    away_shots = pegar_statisticas_time(
        away_stats,
        "Total Shots"
    )

    home_on = pegar_statisticas_time(
        home_stats,
        "Shots on Goal"
    )

    away_on = pegar_statisticas_time(
        away_stats,
        "Shots on Goal"
    )

    home_corners = pegar_statisticas_time(
        home_stats,
        "Corner Kicks"
    )

    away_corners = pegar_statisticas_time(
        away_stats,
        "Corner Kicks"
    )

    home_dangerous = pegar_statisticas_time(
        home_stats,
        "Dangerous Attacks"
    )

    away_dangerous = pegar_statisticas_time(
        away_stats,
        "Dangerous Attacks"
    )

    # ========================================================
    # PRESSÃO
    # ========================================================

    pressao_home = (
        home_posse * 0.20
        + home_shots * 1.0
        + home_on * 1.5
        + home_corners * 1.2
        + home_dangerous * 0.20
    )

    pressao_away = (
        away_posse * 0.20
        + away_shots * 1.0
        + away_on * 1.5
        + away_corners * 1.2
        + away_dangerous * 0.20
    )

    texto = (
        f"🔴 **JOGO AO VIVO**\n\n"
        f"⚽ **{home_name} "
        f"{gols_home} x "
        f"{gols_away} "
        f"{away_name}**\n"
        f"⏱️ Minuto: {elapsed}'\n\n"
        f"📊 **ESTATÍSTICAS**\n\n"
        f"🏠 {home_name}\n"
        f"• Posse: {home_posse:.0f}%\n"
        f"• Chutes: {home_shots:.0f}\n"
        f"• Chutes no gol: {home_on:.0f}\n"
        f"• Escanteios: {home_corners:.0f}\n"
        f"• Ataques perigosos: "
        f"{home_dangerous:.0f}\n\n"
        f"✈️ {away_name}\n"
        f"• Posse: {away_posse:.0f}%\n"
        f"• Chutes: {away_shots:.0f}\n"
        f"• Chutes no gol: {away_on:.0f}\n"
        f"• Escanteios: {away_corners:.0f}\n"
        f"• Ataques perigosos: "
        f"{away_dangerous:.0f}\n\n"
    )

    if pressao_home > pressao_away * 1.20:

        texto += (
            f"🔥 **Maior pressão: "
            f"{home_name}**\n"
        )

    elif pressao_away > pressao_home * 1.20:

        texto += (
            f"🔥 **Maior pressão: "
            f"{away_name}**\n"
        )

    else:

        texto += (
            "⚖️ **Pressão equilibrada**\n"
        )

    if (
        home_corners + away_corners >= 7
        and elapsed != "?"
    ):

        texto += (
            "\n🎯 **Indicador:** "
            "mercado de escanteios "
            "pode estar interessante.\n"
        )

    if (
        home_shots + away_shots >= 12
        and elapsed != "?"
    ):

        texto += (
            "\n⚽ **Indicador:** "
            "volume ofensivo elevado.\n"
        )

    texto += (
        "\n⚠️ Análise estatística informativa. "
        "Não garante resultado."
    )

    return texto


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    global bot

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN não configurado."
        )

        return

    bot = telebot.TeleBot(
        BOT_TOKEN,
        parse_mode="Markdown"
    )

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    @bot.message_handler(
        commands=["start"]
    )
    def start(message):

        bot.send_message(
            message.chat.id,
            "🤖 **BOT DE ANÁLISE DE FUTEBOL**\n\n"
            "🟢 Bot conectado.\n\n"
            "📡 Fonte de dados: "
            "API-Sports / API-Football\n\n"
            "Comandos disponíveis:\n\n"
            "⚽ /jogos — Jogos do dia\n"
            "🔴 /live — Jogos ao vivo\n"
            "🔎 /analisar ID — Analisar um jogo\n"
            "📡 /status — Ver conexão\n"
            "⏸️ /pausar — Pausar bot\n"
            "▶️ /ativar — Ativar bot\n\n"
            "A análise considera estatísticas, "
            "forma recente e pressão do jogo.\n\n"
            "ℹ️ As análises são informativas "
            "e não garantem resultados."
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    @bot.message_handler(
        commands=["status"]
    )
    def status(message):

        ok, informacao = verificar_api()

        if ok:

            bot.send_message(
                message.chat.id,
                "🟢 **API ONLINE**\n\n"
                f"{informacao}"
            )

        else:

            bot.send_message(
                message.chat.id,
                "🔴 **API OFFLINE**\n\n"
                f"{informacao}"
            )

    # --------------------------------------------------------
    # PAUSAR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # ATIVAR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # JOGOS DO DIA
    # --------------------------------------------------------

    @bot.message_handler(
        commands=["jogos"]
    )
    def jogos(message):

        if not BOT_ATIVO:

            bot.send_message(
                message.chat.id,
                "⏸️ O bot está pausado."
            )

            return

        bot.send_message(
            message.chat.id,
            "🔎 **Buscando os jogos do dia...**\n\n"
            "Vou consultar os dados disponíveis."
        )

        try:

            from datetime import datetime
            from zoneinfo import ZoneInfo

            agora = datetime.now(
                ZoneInfo("America/Sao_Paulo")
            )

            data = agora.strftime(
                "%Y-%m-%d"
            )

            lista = jogos_do_dia(data)

            if not lista:

                bot.send_message(
                    message.chat.id,
                    "⚠️ Nenhum jogo encontrado "
                    "nas competições configuradas."
                )

                return

            # Limite para não mandar uma mensagem
            # gigantesca de uma vez
            contador = 0

            for jogo in lista:

                texto = formatar_jogo(jogo)

                bot.send_message(
                    message.chat.id,
                    texto
                )

                contador += 1

                # Pequena pausa entre mensagens
                time.sleep(1)

                if contador >= 20:

                    break

        except Exception as e:

            print(
                f"❌ Erro /jogos: {e}"
            )

            bot.send_message(
                message.chat.id,
                "❌ Ocorreu um erro ao "
                "buscar os jogos."
            )

    # --------------------------------------------------------
    # LIVE
    # --------------------------------------------------------

    @bot.message_handler(
        commands=["live"]
    )
    def live(message):

        if not BOT_ATIVO:

            bot.send_message(
                message.chat.id,
                "⏸️ O bot está pausado."
            )

            return

        bot.send_message(
            message.chat.id,
            "🔴 **Consultando jogos ao vivo...**"
        )

        try:

            lista = jogos_live()

            if not lista:

                bot.send_message(
                    message.chat.id,
                    "⚽ Nenhum jogo ao vivo "
                    "encontrado neste momento."
                )

                return

            for jogo in lista[:10]:

                texto = analisar_live(
                    jogo
                )

                bot.send_message(
                    message.chat.id,
                    texto
                )

                time.sleep(1)

        except Exception as e:

            print(
                f"❌ Erro /live: {e}"
            )

            bot.send_message(
                message.chat.id,
                "❌ Erro ao consultar "
                "jogos ao vivo."
            )

    # --------------------------------------------------------
    # ANALISAR ID
    # --------------------------------------------------------

    @bot.message_handler(
        commands=["analisar"]
    )
    def analisar(message):

        partes = message.text.split()

        if len(partes) < 2:

            bot.send_message(
                message.chat.id,
                "Use assim:\n\n"
                "`/analisar ID`\n\n"
                "Exemplo:\n"
                "`/analisar 123456`"
            )

            return

        try:

            fixture_id = int(partes[1])

        except:

            bot.send_message(
                message.chat.id,
                "❌ ID inválido."
            )

            return

        bot.send_message(
            message.chat.id,
            "🔎 Buscando partida..."
        )

        try:

            dados = api_get(
                "/fixtures",
                {
                    "id": fixture_id,
                    "timezone": "America/Sao_Paulo"
                }
            )

            if not dados:

                bot.send_message(
                    message.chat.id,
                    "❌ Não foi possível "
                    "consultar essa partida."
                )

                return

            lista = dados.get(
                "response",
                []
            )

            if not lista:

                bot.send_message(
                    message.chat.id,
                    "❌ Partida não encontrada."
                )

                return

            jogo = lista[0]

            texto = formatar_jogo(
                jogo
            )

            bot.send_message(
                message.chat.id,
                texto
            )

        except Exception as e:

            print(
                f"❌ Erro /analisar: {e}"
            )

            bot.send_message(
                message.chat.id,
                "❌ Erro ao analisar "
                "a partida."
            )

    # --------------------------------------------------------
    # LOOP TELEGRAM
    # --------------------------------------------------------

    while True:

        try:

            print(
                "🤖 Iniciando Telegram..."
            )

            bot.remove_webhook()

            time.sleep(1)

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                f"❌ Erro Telegram: {e}"
            )

            time.sleep(10)


# ============================================================
# FLASK / RENDER
# ============================================================

@app.route("/")
def home():

    return "Bot funcionando!"


@app.route("/health")
def health():

    return {
        "status": "online",
        "bot": "telegram",
        "api": "API-Sports / API-Football"
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("🤖 BOT DE FUTEBOL")
    print("📡 API-Sports / API-Football")
    print("=" * 50)

    if not BOT_TOKEN:

        print(
            "❌ ERRO: BOT_TOKEN não encontrado."
        )

    elif not API_FOOTBALL_KEY:

        print(
            "❌ ERRO: API_FOOTBALL_KEY "
            "não encontrada."
        )

    else:

        print(
            "✅ Variáveis de ambiente encontradas."
        )

    thread = threading.Thread(
        target=iniciar_bot,
        daemon=True
    )

    thread.start()

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
