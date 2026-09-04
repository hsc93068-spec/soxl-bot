import os
import time
import json
import requests
import yfinance as yf
from flask import Flask
from threading import Thread

# 콘솔 출력 버퍼링 해제 (Render 로그 즉시 출력)
os.environ["PYTHONUNBUFFERED"] = "1"

# ==========================================
# 1. 기본 설정 및 텔레그램 발송 함수
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
        
        # 1차 시도: Markdown 파싱 포함 발송
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            res = requests.post(url, data=payload, timeout=10)
            if res.status_code == 200:
                print(f"[텔레그램 발송 성공] Status: {res.status_code}", flush=True)
            else:
                print(f"[텔레그램 1차 실패] Status: {res.status_code} | Res: {res.text}", flush=True)
                # Markdown 문법 에러 가능성이 있으므로 parse_mode를 제거하고 재시도
                payload.pop("parse_mode", None)
                res_retry = requests.post(url, data=payload, timeout=10)
                print(f"[텔레그램 재시도 결과] Status: {res_retry.status_code} | Res: {res_retry.text}", flush=True)
        except Exception as e:
            print(f"텔레그램 발송 예외 오류: {e}", flush=True)

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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.cnn.com/markets/fear-and-greed"
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

    # 3시간(10,800초) 지났는지 체크
    if (now - last_time) >= 10800:
        # 최초 발송이거나 최근 보낸 점수와 5pt 이상 차이나는 경우 알람
        if last_fg is None or abs(current_fg - last_fg) >= 5:
            msg = f"📊 *[공포&탐욕 지수 알림]*\n• 현재 공탐지수: *{current_fg}pt*"
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

    # 3시간(10,800초) 지났는지 체크
    if (now - last_time) >= 10800:
        # 최초 발송이거나 최근 보낸 점수와 2pt 초과 차이나는 경우 알람 (2pt 내외이면 안 보냄)
        if last_vix is None or abs(current_vix - last_vix) > 2.0:
            msg = f"📉 *[VIX 변동성 지수 알림]*\n• 현재 VIX: *{current_vix:.2f}pt*"
            send_telegram_msg(msg)
            state["last_vix_level"] = current_vix
            state["last_vix_time"] = now

    return state

# ==========================================
# 4. 하락장 판정 로직
# ==========================================
def is_bear_market(fg_score, vix_score):
    # 하락장 조건: 공탐지수 <= 40 이고 VIX >= 20 (2가지 조건 모두 충족 시)
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

        df["RSI"] = calculate_rsi(df["Close"])
        current_rsi = round(df["RSI"].iloc[-1], 2)
        current_price = round(df["Close"].iloc[-1], 2)

        # 최근 10개 봉 중 최저 RSI
        recent_rsi_min = df["RSI"].tail(10).min()

        # 조건 1: RSI 가 기준값 이하로 진입했었는지 확인
        if recent_rsi_min <= threshold_rsi:
            bounce = current_rsi - recent_rsi_min

            # 조건 2: 최저점 대비 지정 bounce_pt 이상 반등 및 10pt 이상 과도한 반등(노이즈) 제외
            if bounce >= bounce_pt and bounce < noise_limit:
                history_key = f"{ticker_symbol}_{interval_name}"
                history = state["rsi_history"].get(history_key, {})

                last_alert_time = history.get("time", 0)
                last_min_rsi = history.get("min_rsi", -999)

                now = time.time()
                time_passed = (now - last_alert_time) / 60  # 분 단위

                # 조건 3: 최근 60분 이내 발송건 중 최저점 차이가 dup_limit 이내이면 알람 제외
                if last_alert_time > 0 and time_passed <= 60:
                    if abs(recent_rsi_min - last_min_rsi) <= dup_limit:
                        return state

                # 조건 만족 시 알림 발송
                msg = f"🔔 *[{ticker_symbol} RSI 실시간 반등 알림 ({interval_name})]*\n"
                msg += f"• 현재가: *${current_price:,}*\n"
                msg += f"• 실시간 RSI: *{current_rsi}pt*\n"
                msg += f"• 구간 최저 RSI: *{round(recent_rsi_min, 2)}pt* (반등: *+{round(bounce, 2)}pt*)"

                send_telegram_msg(msg)

                state["rsi_history"][history_key] = {
                    "time": now,
                    "min_rsi": recent_rsi_min,
                }

    except Exception as e:
        print(f"[{ticker_symbol}] {interval_name} RSI 계산 오류: {e}", flush=True)

    return state

def check_all_asset_rsi_alerts(fg_score, vix_score, state):
    # 하락장(공탐지수 <= 40 AND VIX >= 20) 조건 충족 시 알람 1, 2, 3 모두 안 보냄
    if is_bear_market(fg_score, vix_score):
        print("⛔ 하락장 조건 충족 (공탐<=40 & VIX>=20) -> RSI 알람 발송 중단", flush=True)
        return state

    for ticker in TARGET_ASSETS:
        # 알람1 (15분봉): RSI <= 30, 반등 >= 4pt, 60분내 최저점차 2pt 이내 차단
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

        # 알람2 (30분봉): RSI <= 33, 반등 >= 3pt, 60분내 최저점차 1pt 이내 차단
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

        # 알람3 (60분봉): RSI <= 36, 반등 >= 2pt, 60분내 최저점차 0.5pt 이내 차단
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

        time.sleep(1) # 야후 API 과부하 방지 간격

    return state

def run_trading_system():
    print("=== [실시간 감시] 시세 및 지표 점검 중 ===", flush=True)
    state = load_state()

    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()

    # 알람 4 점검
    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)

    # 알람 1, 2, 3 점검
    state = check_all_asset_rsi_alerts(current_fg, current_vix, state)

    save_state(state)

# ==========================================
# 6. 실시간 감시 루프
# ==========================================
def worker_loop():
    send_telegram_msg("🚀 *[실시간 감시 모드 정상 작동 중]*\nBTC-USD, VOO, QQQ, SOXX, DIA 감시를 시작합니다.")
    
    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"메인 루프 에러: {e}", flush=True)
        
        # 2분(120초) 간격 반복
        time.sleep(120)

t = Thread(target=worker_loop)
t.daemon = True
t.start()

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
