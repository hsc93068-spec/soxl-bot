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

# ⚠️ 반드시 @BotFather에서 확인한 최신 올바른 토큰을 대입해 주세요.
TELEGRAM_TOKEN = "8986570820:AAFfJht2Y02m21_T7SOvSfss0nPozDPTSpg"
CHAT_ID = "1157818555"

TARGET_ASSETS = ["QQQ", "SOXX", "DIA", "VOO", "BTC-USD"]

def send_telegram_msg(message):
    print(f"[텔레그램 발송 시도]\n{message}\n", flush=True)
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            res = requests.post(
                url, data={"chat_id": CHAT_ID, "text": message}, timeout=10
            )
            print(f"[텔레그램 응답] Status: {res.status_code} | Response: {res.text}", flush=True)
        except Exception as e:
            print(f"텔레그램 발송 오류: {e}", flush=True)

def load_state():
    default_state = {
        "last_fg_score": 100,
        "last_vix_level": 0,
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
# 2. 데이터 수집 (Yahoo 429 차단 방지 적용)
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

def check_individual_ma20_status(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo", interval="1d")
        if df.empty or len(df) < 20:
            return False, False
        df["MA20"] = df["Close"].rolling(20).mean()

        is_above = df["Close"].iloc[-1] > df["MA20"].iloc[-1]
        is_upward = df["MA20"].iloc[-1] > df["MA20"].iloc[-2]
        return is_above, is_upward
    except Exception:
        return False, False

# ==========================================
# 3. 공탐지수 & VIX 마디가 전용 알림
# ==========================================
def check_fear_and_greed_alert(current_fg, state):
    if current_fg is None:
        return state
    last_fg = state.get("last_fg_score", 100)

    if current_fg > 60:
        state["last_fg_score"] = 100
        return state

    if current_fg <= 60 and (last_fg - current_fg) >= 5:
        target_step = (current_fg // 5) * 5
        msg = f"🚨 [공포지수 하향 돌파 알림]\n• 현재 공탐지수: {current_fg}pt (단계: {target_step}pt 이하)\n\n"

        if current_fg <= 10:
            msg += "🔥 [대바닥 3차] 지표 무관 총 투자금 추가 10% (누적 30%) 진입!"
        elif current_fg <= 20:
            msg += "⚠️ [극단 공포 2차] 지표 무관 총 투자금 추가 10% (누적 20%) 진입!"
        elif current_fg <= 30:
            msg += "📢 [공포 진입 1차] 지표 무관 총 투자금 10% 1차 진입!"
        else:
            msg += "⛔ 하락/관망 구간 (일반 RSI 매수 금지 유지)"

        send_telegram_msg(msg)
        state["last_fg_score"] = target_step

    return state

def check_vix_alert(current_vix, state):
    if current_vix is None:
        return state
    last_vix_level = state.get("last_vix_level", 0)

    if current_vix <= 17.0:
        if last_vix_level != 0:
            state["last_vix_level"] = 0
        return state

    if current_vix >= 22.0:
        state["last_vix_level"] = 22
        return state

    current_level = int(current_vix)

    if current_level != last_vix_level:
        msg = f"⚠️ [VIX 변동성 마디가 알림]\n• 현재 VIX: {current_vix:.2f} (마디가: {current_level}pt대)\n\n"

        if current_level == 17 and last_vix_level == 0:
            msg += "📢 VIX 17.0 초과 진입! (상승장 경계선 이탈, 변동성 주의)"
        elif current_level == 18:
            msg += "📢 VIX 18pt 도달 (횡보/변동성 확대 구간)"
        elif current_level == 19:
            msg += "⚠️ VIX 19pt 도달 (시장 경계 심화)"
        elif current_level == 20:
            msg += "🚨 VIX 20pt 도달 (하락장 경계선 진입 - 일반 RSI 매수 금지 가동)"
        elif current_level == 21:
            msg += "🚨 VIX 21pt 도달 (공포 심화, 추가 폭락 주의)"
        elif last_vix_level == 22 and current_level == 21:
            msg += "🔄 VIX 22pt 미만으로 복귀 (극단 공포 소폭 완화)"

        send_telegram_msg(msg)
        state["last_vix_level"] = current_level

    return state

# ==========================================
# 4. 개별 종목 기준 국면 판정 로직
# ==========================================
def get_individual_regime(is_above, is_upward, fg_score, vix_score):
    cond_ma20 = is_above and is_upward
    cond_fg = (fg_score >= 50) if fg_score is not None else True
    cond_vix = (vix_score <= 20) if vix_score is not None else True

    score = int(cond_ma20) + int(cond_fg) + int(cond_vix)

    if score == 3:
        if (fg_score and fg_score >= 60) and (vix_score and vix_score <= 17):
            return "BULL"
        return "RANGE"
    elif score >= 2:
        return "RANGE"
    else:
        return "BEAR"

# ==========================================
# 5. 1분 실시간 RSI 반등 알림 계산 및 필터링
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

        recent_rsi_min = df["RSI"].tail(10).min()

        if recent_rsi_min <= threshold_rsi:
            bounce = current_rsi - recent_rsi_min

            if bounce >= bounce_pt and bounce < noise_limit:
                history_key = f"{ticker_symbol}_{interval_name}"
                history = state["rsi_history"].get(history_key, {})

                last_alert_time = history.get("time", 0)
                last_min_rsi = history.get("min_rsi", -999)

                now = time.time()
                time_passed = (now - last_alert_time) / 60

                if last_alert_time > 0 and time_passed <= 60:
                    if abs(recent_rsi_min - last_min_rsi) <= dup_limit:
                        return state

                msg = f"🔔 [{ticker_symbol} RSI 실시간 반등 알림 ({interval_name})]\n"
                msg += f"• 현재가: ${current_price}\n"
                msg += f"• 실시간 RSI: {current_rsi}pt\n"
                msg += f"• 구간 최저 RSI: {round(recent_rsi_min, 2)}pt (반등: +{round(bounce, 2)}pt)"

                send_telegram_msg(msg)

                state["rsi_history"][history_key] = {
                    "time": now,
                    "min_rsi": recent_rsi_min,
                }

    except Exception as e:
        print(f"[{ticker_symbol}] {interval_name} RSI 계산 오류: {e}", flush=True)

    return state

def check_all_asset_rsi_alerts(fg_score, vix_score, state):
    for ticker in TARGET_ASSETS:
        is_above, is_upward = check_individual_ma20_status(ticker)
        regime = get_individual_regime(
            is_above, is_upward, fg_score, vix_score
        )

        if regime == "BEAR":
            continue

        if regime == "BULL":
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

        elif regime == "RANGE":
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
        time.sleep(1) # API 요청 과부하 방지 간격

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
# 6. 실시간 감시 루프 (야후 API 차단 방지 간격 조정)
# ==========================================
def worker_loop():
    send_telegram_msg("🚀 [실시간 감시 모드 정상 작동 중]\n봇이 실시간 시세 감시를 다시 시작했습니다.")
    
    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"메인 루프 에러: {e}", flush=True)
        
        # 야후 파이낸스 IP 차단을 피하기 위한 2분(120초) 간격 설정
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
