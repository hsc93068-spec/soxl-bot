import json
import os
import time
from threading import Thread
from flask import Flask
import requests
import yfinance as yf

# 콘솔 출력 버퍼링 해제 (Render 로그 즉시 출력)
os.environ["PYTHONUNBUFFERED"] = "1"

# ==========================================
# 1. 기본 설정 및 텔레그램 발송 함수
# ==========================================
STATE_FILE = "trading_bot_state.json"

TELEGRAM_TOKEN = "8986570820:AAG_vdH9n27dDcxY3W7JkDrmCHgpAxiP3RQ"
CHAT_ID = "1157818555"

TARGET_ASSETS = ["BTC-USD", "VOO", "QQQ", "SOXX", "DIA"]


def send_telegram_msg(message):
    print(f"[텔레그램 발송 시도]\n{message}\n", flush=True)
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        try:
            res = requests.post(url, data=payload, timeout=10)
            if res.status_code != 200:
                payload.pop("parse_mode", None)
                requests.post(url, data=payload, timeout=10)
        except Exception as e:
            print(f"텔레그램 발송 예외 오류: {e}", flush=True)


# [수정된 핵심 로직] 트레이딩뷰와 동일한 와일더 평활법(Wilder's Smoothing) RSI 계산
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # 트레이딩뷰의 RMA(Wilder's Smoothing) 방식 적용
    avg_gain = gain.ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ==========================================
# 2. 데이터 조회 전용 헬퍼 함수
# ==========================================
def get_fear_and_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return round(res.json()["fear_and_greed"]["score"])
        return 50
    except Exception:
        return 50


def get_vix_index():
    try:
        ticker = yf.Ticker("^VIX")
        vix = ticker.history(period="1d")
        if vix.empty:
            return 18.0
        return round(vix["Close"].iloc[-1], 2)
    except Exception:
        return 18.0


def is_bear_market(fg_score, vix_score):
    if fg_score is not None and vix_score is not None:
        if fg_score <= 40 and vix_score >= 20.0:
            return True
    return False


def get_all_assets_rsi_info(timeframe):
    results = []
    for ticker in TARGET_ASSETS:
        try:
            # Wilder RSI의 충분한 지표 계산을 위해 14일 치 데이터를 수집
            df = yf.Ticker(ticker).history(period="14d", interval=timeframe)
            if not df.empty and len(df) >= 20:
                df["RSI"] = calculate_rsi(df["Close"])
                current_rsi = round(df["RSI"].iloc[-1], 2)
                current_price = round(df["Close"].iloc[-1], 2)
                results.append(
                    f"• *{ticker}*: ${current_price:,} (RSI:"
                    f" *{current_rsi}pt*)"
                )
            else:
                results.append(f"• *{ticker}*: 데이터 부족")
        except Exception:
            results.append(f"• *{ticker}*: 조회 실패")
    return "\n".join(results)


def get_single_asset_detail(ticker_symbol):
    try:
        df_15m = yf.Ticker(ticker_symbol).history(
            period="14d", interval="15m"
        )
        df_30m = yf.Ticker(ticker_symbol).history(
            period="14d", interval="30m"
        )
        df_60m = yf.Ticker(ticker_symbol).history(
            period="14d", interval="60m"
        )
        df_1d = yf.Ticker(ticker_symbol).history(period="3mo", interval="1d")

        if df_30m.empty:
            return f"❌ *{ticker_symbol}* 데이터를 불러올 수 없습니다."

        current_price = round(df_30m["Close"].iloc[-1], 2)

        rsi_15m = round(calculate_rsi(df_15m["Close"]).iloc[-1], 2)
        rsi_30m = round(calculate_rsi(df_30m["Close"]).iloc[-1], 2)
        rsi_60m = round(calculate_rsi(df_60m["Close"]).iloc[-1], 2)
        rsi_1d = round(calculate_rsi(df_1d["Close"]).iloc[-1], 2)

        msg = (
            f"📌 *[{ticker_symbol} 상세 정보]*\n"
            f"• 현재가: *${current_price:,}*\n"
            f"• 15분봉 RSI: *{rsi_15m}pt*\n"
            f"• 30분봉 RSI: *{rsi_30m}pt*\n"
            f"• 60분봉 RSI: *{rsi_60m}pt*\n"
            f"• 일봉 RSI: *{rsi_1d}pt*"
        )
        return msg
    except Exception as e:
        return f"❌ 조회 중 오류 발생: {e}"


def get_summary_briefing():
    fg = get_fear_and_greed()
    vix = get_vix_index()
    bear_status = (
        "⛔ 발송 중단 (하락장 조건 충족)"
        if is_bear_market(fg, vix)
        else "✅ 정상 동작 중"
    )

    asset_infos = get_all_assets_rsi_info("30m")

    msg = (
        "📊 *[전체 요약 브리핑]*\n"
        f"• 공포&탐욕 지수: *{fg}pt*\n"
        f"• VIX 변동성 지수: *{vix:.2f}pt*\n"
        f"• RSI 알림 필터: *{bear_status}*\n"
        "───────────────\n"
        f"📈 *종목별 현재가 및 30분봉 RSI*\n{asset_infos}"
    )
    return msg


def get_help_message():
    return (
        "💡 *[텔레그램 봇 명령어/질문 안내]*\n\n"
        "• *요약 / 브리핑*: 전체 지표 및 종목 30분봉 RSI 종합 보고서\n"
        "• *15분봉 / 30분봉 / 60분봉 / 일봉*: 타임프레임별 전체 RSI 조회\n"
        "• *종목명 (예: QQQ, 비트코인)*: 해당 종목 타임프레임별 상세 RSI 조회\n"
        "• *공탐 / 공포*: 현재 공포&탐욕 지수 조회\n"
        "• *빅스 / VIX*: 현재 VIX 변동성 지수 조회\n"
        "• *하락장 / 필터*: 하락장 감시 필터 동작 여부 확인\n"
        "• *종목 / 감시*: 감시 대상 종목 목록 확인\n"
        "• *상태 / /status*: 봇 작동 상태 확인"
    )


# ==========================================
# 3. 텔레그램 메시지 실시간 수신 (Listener)
# ==========================================
def telegram_listener_loop():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            res = requests.get(url, timeout=35).json()

            if res.get("ok"):
                for result in res.get("result", []):
                    offset = result["update_id"] + 1
                    message = result.get("message", {})
                    text = message.get("text", "").strip()
                    user_chat_id = str(message.get("chat", {}).get("id", ""))

                    if text and user_chat_id == CHAT_ID:
                        text_lower = text.lower()

                        if (
                            "도움말" in text
                            or "메뉴" in text
                            or text in ["/help", "?", "명령어"]
                        ):
                            send_telegram_msg(get_help_message())

                        elif (
                            "요약" in text
                            or "전체" in text
                            or "브리핑" in text
                        ):
                            send_telegram_msg("⏳ *[전체 요약 브리핑 생성 중...]*")
                            send_telegram_msg(get_summary_briefing())

                        elif "하락장" in text or "필터" in text:
                            fg = get_fear_and_greed()
                            vix = get_vix_index()
                            if is_bear_market(fg, vix):
                                reply_msg = (
                                    "⛔ *[하락장 필터 작동 중]*\n공탐지수"
                                    f" ({fg}pt <= 40) 및 VIX ({vix:.2f}pt >="
                                    " 20) 조건 충족으로 인해 *RSI 알림 발송이"
                                    " 일시 중단*되었습니다."
                                )
                            else:
                                reply_msg = (
                                    "✅ *[필터 미작동 (상승/보호장)]*\n공탐지수:"
                                    f" *{fg}pt*, VIX: *{vix:.2f}pt*\n현재 RSI"
                                    " 반등 알림이 정상 감시 중입니다."
                                )
                            send_telegram_msg(reply_msg)

                        elif "일봉" in text:
                            send_telegram_msg("⏳ *[일봉 RSI 데이터 조회 중...]*")
                            rsi_info = get_all_assets_rsi_info("1d")
                            reply_msg = (
                                f"📅 *[현재 각 종목별 일봉 RSI]*\n{rsi_info}"
                            )
                            send_telegram_msg(reply_msg)

                        elif "15분" in text:
                            send_telegram_msg(
                                "⏳ *[15분봉 RSI 데이터 조회 중...]*"
                            )
                            send_telegram_msg(
                                "📈 *[현재 각 종목별 15분봉"
                                f" RSI]*\n{get_all_assets_rsi_info('15m')}"
                            )

                        elif "30분" in text:
                            send_telegram_msg(
                                "⏳ *[30분봉 RSI 데이터 조회 중...]*"
                            )
                            send_telegram_msg(
                                "📈 *[현재 각 종목별 30분봉"
                                f" RSI]*\n{get_all_assets_rsi_info('30m')}"
                            )

                        elif "60분" in text or "1시간" in text:
                            send_telegram_msg(
                                "⏳ *[60분봉 RSI 데이터 조회 중...]*"
                            )
                            send_telegram_msg(
                                "📈 *[현재 각 종목별 60분봉"
                                f" RSI]*\n{get_all_assets_rsi_info('60m')}"
                            )

                        elif (
                            "비트코인" in text
                            or "btc" in text_lower
                            or "비트" in text
                        ):
                            send_telegram_msg(
                                get_single_asset_detail("BTC-USD")
                            )

                        elif "voo" in text_lower:
                            send_telegram_msg(get_single_asset_detail("VOO"))

                        elif "qqq" in text_lower:
                            send_telegram_msg(get_single_asset_detail("QQQ"))

                        elif "soxx" in text_lower:
                            send_telegram_msg(get_single_asset_detail("SOXX"))

                        elif "dia" in text_lower:
                            send_telegram_msg(get_single_asset_detail("DIA"))

                        elif "공탐" in text or "공포" in text:
                            fg = get_fear_and_greed()
                            send_telegram_msg(
                                f"📊 *[현재 공포&탐욕 지수]*\n• 현재 점수: *{fg}pt*"
                            )

                        elif "빅스" in text or "vix" in text_lower:
                            vix = get_vix_index()
                            send_telegram_msg(
                                "📉 *[현재 VIX 변동성 지수]*\n• 현재 지수:"
                                f" *{vix:.2f}pt*"
                            )

                        elif (
                            "종목" in text
                            or "감시" in text
                            or text == "/list"
                        ):
                            asset_list_str = "\n".join(
                                [f"• *{asset}*" for asset in TARGET_ASSETS]
                            )
                            send_telegram_msg(
                                "📋 *[현재 감시 중인 종목"
                                f" 목록]*\n{asset_list_str}"
                            )

                        elif "상태" in text or text == "/status":
                            send_telegram_msg(
                                "✅ *[봇 정상 작동 중]*\n실시간 시세 감시 및"
                                " RSI 반등 모니터링이 활성화되어 있습니다."
                            )

        except Exception as e:
            print(f"텔레그램 수신 에러: {e}", flush=True)

        time.sleep(2)


# ==========================================
# 4. 데이터 저장/로드 및 주기적 감시 로직
# ==========================================
def load_state():
    default_state = {
        "last_fg_score": None,
        "last_fg_time": 0,
        "last_vix_level": None,
        "last_vix_time": 0,
        "rsi_history": {},
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return {**default_state, **json.load(f)}
        except Exception:
            return default_state
    return default_state


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"상태 저장 오류: {e}", flush=True)


def check_fear_and_greed_alert(current_fg, state):
    if current_fg is None:
        return state
    now = time.time()
    last_fg = state.get("last_fg_score")
    last_time = state.get("last_fg_time", 0)

    if (now - last_time) >= 10800:
        if last_fg is None or abs(current_fg - last_fg) >= 5:
            send_telegram_msg(
                "📊 *[공포&탐욕 지수 알림]*\n• 현재 공탐지수:"
                f" *{current_fg}pt*"
            )
            state["last_fg_score"] = current_fg
            state["last_fg_time"] = now
    return state


def check_vix_alert(current_vix, state):
    if current_vix is None:
        return state
    now = time.time()
    last_vix = state.get("last_vix_level")
    last_time = state.get("last_vix_time", 0)

    if (now - last_time) >= 10800:
        if last_vix is None or abs(current_vix - last_vix) > 2.0:
            send_telegram_msg(
                "📉 *[VIX 변동성 지수 알림]*\n• 현재 VIX:"
                f" *{current_vix:.2f}pt*"
            )
            state["last_vix_level"] = current_vix
            state["last_vix_time"] = now
    return state


def process_rsi_rule(
    ticker_symbol,
    interval_name,
    timeframe,
    threshold_rsi,
    bounce_pt,
    noise_limit,
    dup_limit,
    state,
):
    try:
        df = yf.Ticker(ticker_symbol).history(period="14d", interval=timeframe)
        if df.empty or len(df) < 20:
            return state

        now = time.time()
        last_candle_time = df.index[-1].timestamp()

        # 휴장/주말 예외 처리 (2시간 지난 데이터 스킵)
        if (now - last_candle_time) > 7200:
            return state

        df["RSI"] = calculate_rsi(df["Close"])
        current_rsi = round(df["RSI"].iloc[-1], 2)
        current_price = round(df["Close"].iloc[-1], 2)
        recent_rsi_min = round(df["RSI"].tail(10).min(), 2)

        if recent_rsi_min <= threshold_rsi:
            bounce = current_rsi - recent_rsi_min

            if bounce >= bounce_pt and bounce < noise_limit:
                history_key = f"{ticker_symbol}_{interval_name}"
                history = state["rsi_history"].get(history_key, {})

                last_alert_time = history.get("time", 0)
                last_min_rsi = history.get("min_rsi", -999)
                last_rsi = history.get("current_rsi", -999)

                time_passed = (now - last_alert_time) / 60

                if recent_rsi_min == last_min_rsi and current_rsi == last_rsi:
                    return state

                if last_alert_time > 0 and time_passed <= 60:
                    if abs(recent_rsi_min - last_min_rsi) <= dup_limit:
                        return state

                msg = (
                    f"🔔 *[{ticker_symbol} RSI 실시간 반등 알림"
                    f" ({interval_name})]*\n• 현재가: *${current_price:,}*\n•"
                    f" 실시간 RSI: *{current_rsi}pt*\n• 구간 최저 RSI:"
                    f" *{recent_rsi_min}pt* (반등: *+{round(bounce, 2)}pt*)"
                )
                send_telegram_msg(msg)

                state["rsi_history"][history_key] = {
                    "time": now,
                    "min_rsi": recent_rsi_min,
                    "current_rsi": current_rsi,
                }
    except Exception as e:
        print(
            f"[{ticker_symbol}] {interval_name} RSI 계산 오류: {e}", flush=True
        )

    return state


def check_all_asset_rsi_alerts(fg_score, vix_score, state):
    if is_bear_market(fg_score, vix_score):
        return state

    for ticker in TARGET_ASSETS:
        state = process_rsi_rule(
            ticker,
            "15분봉",
            "15m",
            threshold_rsi=30,
            bounce_pt=4,
            noise_limit=10,
            dup_limit=2.0,
            state=state,
        )
        state = process_rsi_rule(
            ticker,
            "30분봉",
            "30m",
            threshold_rsi=33,
            bounce_pt=3,
            noise_limit=10,
            dup_limit=1.0,
            state=state,
        )
        state = process_rsi_rule(
            ticker,
            "60분봉",
            "60m",
            threshold_rsi=36,
            bounce_pt=2,
            noise_limit=10,
            dup_limit=0.5,
            state=state,
        )
        time.sleep(1)

    return state


def run_trading_system():
    state = load_state()
    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()

    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)
    state = check_all_asset_rsi_alerts(current_fg, current_vix, state)

    save_state(state)


# ==========================================
# 5. 백그라운드 스레드 및 웹서버 실행
# ==========================================
def worker_loop():
    send_telegram_msg(
        "🚀 *[실시간 감시 모드 정상 작동 중]*\nBTC-USD, VOO, QQQ, SOXX, DIA"
        " 감시를 시작합니다.\n(도움말 확인: '도움말' 입력)"
    )
    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"메인 루프 에러: {e}", flush=True)
        time.sleep(120)


Thread(target=worker_loop, daemon=True).start()
Thread(target=telegram_listener_loop, daemon=True).start()

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running real-time!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
