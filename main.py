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
# 1. 기본 설정 및 텔레그램 발송/수신 함수
# ==========================================
STATE_FILE = "trading_bot_state.json"

TELEGRAM_TOKEN = "8986570820:AAG_vdH9n27dDcxY3W7JkDrmCHgpAxiP3RQ"
CHAT_ID = "1157818555"

# 대상 종목: 비트코인, VOO, QQQ, SOXX, DIA (총 5개 종목)
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
            if res.status_code == 200:
                print(
                    f"[텔레그램 발송 성공] Status: {res.status_code}",
                    flush=True,
                )
            else:
                print(
                    f"[텔레그램 1차 실패] Status: {res.status_code} | Res:"
                    f" {res.text}",
                    flush=True,
                )
                payload.pop("parse_mode", None)
                res_retry = requests.post(url, data=payload, timeout=10)
                print(
                    f"[텔레그램 재시도 결과] Status: {res_retry.status_code} |"
                    f" Res: {res_retry.text}",
                    flush=True,
                )
        except Exception as e:
            print(f"텔레그램 발송 예외 오류: {e}", flush=True)


# [신규 추가] 사용자의 텔레그램 메시지를 실시간으로 수신하여 답변하는 스레드
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

                    # 등록된 본인 CHAT_ID의 메시지만 처리
                    if text and user_chat_id == CHAT_ID:
                        if (
                            "종목" in text
                            or "감시" in text
                            or text == "/list"
                        ):
                            asset_list_str = "\n".join(
                                [f"• *{asset}*" for asset in TARGET_ASSETS]
                            )
                            reply_msg = (
                                "📋 *[현재 감시 중인 종목"
                                f" 목록]*\n{asset_list_str}"
                            )
                            send_telegram_msg(reply_msg)
                        elif "상태" in text or text == "/status":
                            reply_msg = (
                                "✅ *[봇 정상 작동 중]*\n실시간 시세 감시 및"
                                " RSI 반등 모니터링이 활성화되어 있습니다."
                            )
                            send_telegram_msg(reply_msg)

        except Exception as e:
            print(f"텔레그램 수신 에러: {e}", flush=True)

        time.sleep(2)


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


# ==========================================
# 2. 데이터 수집
# ==========================================
def get_fear_and_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return round(res.json()["fear_and_greed"]["score"])
        else:
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


# ==========================================
# 3. 알람4: 공탐지수 & VIX (3시간 주기 및 변동폭 검사)
# ==========================================
def check_fear_and_greed_alert(current_fg, state):
    if current_fg is None:
        return state

    now = time.time()
    last_fg = state.get("last_fg_score")
    last_time = state.get("last_fg_time", 0)

    if (now - last_time) >= 10800:
        if last_fg is None or abs(current_fg - last_fg) >= 5:
            msg = (
                "📊 *[공포&탐욕 지수 알림]*\n• 현재 공탐지수:"
                f" *{current_fg}pt*"
            )
            send_telegram_msg(msg)
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
            msg = (
                "📉 *[VIX 변동성 지수 알림]*\n• 현재 VIX:"
                f" *{current_vix:.2f}pt*"
            )
            send_telegram_msg(msg)
            state["last_vix_level"] = current_vix
            state["last_vix_time"] = now

    return state


# ==========================================
# 4. 하락장 판정 로직
# ==========================================
def is_bear_market(fg_score, vix_score):
    if fg_score is not None and vix_score is not None:
        if fg_score <= 40 and vix_score >= 20.0:
            return True
    return False


# ==========================================
# 5. 실시간 RSI 반등 알림 계산 및 필터링
# ==========================================
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


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
        df = yf.Ticker(ticker_symbol).history(period="5d", interval=timeframe)
        if df.empty or len(df) < 20:
            return state

        now = time.time()
        last_candle_time = df.index[-1].timestamp()

        # [휴장/주말 예외 처리] 마지막 봉의 생성 시각이 현재 시각 기준 2시간(7,200초) 이상 지났다면 스킵
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
                    f" ({interval_name})]*\n"
                )
                msg += f"• 현재가: *${current_price:,}*\n"
                msg += f"• 실시간 RSI: *{current_rsi}pt*\n"
                msg += (
                    f"• 구간 최저 RSI: *{recent_rsi_min}pt* (반등:"
                    f" *+{round(bounce, 2)}pt*)"
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
        print(
            "⛔ 하락장 조건 충족 (공탐<=40 & VIX>=20) -> RSI 알람 발송 중단",
            flush=True,
        )
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
    print("=== [실시간 감시] 시세 및 지표 점검 중 ===", flush=True)
    state = load_state()

    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()

    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)

    state = check_all_asset_rsi_alerts(current_fg, current_vix, state)

    save_state(state)


# ==========================================
# 6. 백그라운드 스레드 실행
# ==========================================
def worker_loop():
    send_telegram_msg(
        "🚀 *[실시간 감시 모드 정상 작동 중]*\nBTC-USD, VOO, QQQ, SOXX, DIA"
        " 감시를 시작합니다."
    )

    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"메인 루프 에러: {e}", flush=True)

        time.sleep(120)


# 1) 감시 루프 스레드 실행
t_worker = Thread(target=worker_loop)
t_worker.daemon = True
t_worker.start()

# 2) [신규] 메시지 수신 스레드 실행
t_listener = Thread(target=telegram_listener_loop)
t_listener.daemon = True
t_listener.start()

# ==========================================
# 7. Render 바인딩용 Flask 웹서버
# ==========================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running real-time!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
