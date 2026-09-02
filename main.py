import json
import os
from threading import Thread
import time
from flask import Flask
import requests
import yfinance as yf

# ==========================================
# 1. Render 포트 감지용 웹서버 (Flask)
# ==========================================
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


Thread(target=run_flask).start()

# ==========================================
# 2. 설정 및 상태 저장 파일
# ==========================================
STATE_FILE = "macro_alert_state.json"
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # 본인의 토큰 입력
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"  # 본인의 챗 ID 입력

TARGET_ASSETS = ["QQQ", "SOXX", "DIA", "VOO", "BTC-USD"]  # 감시 대상 자산


def send_telegram_msg(message):
    print(f"[텔레그램 발송]\n{message}\n")
    if TELEGRAM_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN":
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, data={"chat_id": CHAT_ID, "text": message})
        except Exception as e:
            print(f"텔레그램 발송 실패: {e}")


def load_alert_state():
    default_state = {"last_fg_score": 100, "last_vix_level": 0}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return {**default_state, **json.load(f)}
        except Exception:
            return default_state
    return default_state


def save_alert_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ==========================================
# 3. 매크로 지표 데이터 수집 (CNN F&G, VIX, 20일선)
# ==========================================
def get_fear_and_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return round(res.json()["fear_and_greed"]["score"])
    except Exception as e:
        print(f"[오류] 공탐지수 수집 실패: {e}")
        return None


def get_vix_index():
    try:
        vix = yf.Ticker("^VIX").history(period="1d")
        return round(vix["Close"].iloc[-1], 2)
    except Exception as e:
        print(f"[오류] VIX 수집 실패: {e}")
        return None


def check_ma20_up(ticker_symbol="QQQ"):
    """QQQ 기준 일봉 20일선 우상향 여부 및 주가 위치 판정"""
    try:
        df = yf.Ticker(ticker_symbol).history(period="2m", interval="1d")
        if len(df) < 20:
            return False
        df["MA20"] = df["Close"].rolling(20).mean()
        # 오늘 주가가 20일선 위이고, 20일선이 전일 대비 상승했는지 체크
        is_above = df["Close"].iloc[-1] > df["MA20"].iloc[-1]
        is_upward = df["MA20"].iloc[-1] > df["MA20"].iloc[-2]
        return is_above and is_upward
    except Exception as e:
        print(f"[오류] MA20 수집 실패: {e}")
        return False


# ==========================================
# 4. 공탐지수 / VIX 전용 마디가 알림
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
        msg = f"🚨 [공포지수 하향 돌파 알림]\n• 현재 공탐지수: {current_fg}pt (기준 단계: {target_step}pt 이하)\n\n"

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
# 5. 시장 국면 판정 로직
# ==========================================
def get_market_regime(ma20_up, fg_score, vix_score):
    # 하락장 조건 (1가지라도 충족 시 하락장)
    if (
        not ma20_up
        or (fg_score and fg_score <= 40)
        or (vix_score and vix_score >= 20)
    ):
        return "BEAR"

    # 상승장 조건 (3가지 모두 충족 시)
    if ma20_up and (fg_score and fg_score >= 60) and (vix_score and vix_score <= 17):
        return "BULL"

    # 횡보장 조건 (그 외)
    return "RANGE"


# ==========================================
# 6. 개별 자산(QQQ, SOXX 등) RSI 계산 및 알림 감시
# ==========================================
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def check_asset_rsi_alert(ticker_symbol, regime):
    # 하락장(BEAR)일 경우 일반 RSI 알림 전면 뮤트
    if regime == "BEAR":
        return

    # 상승장은 15분봉, 횡보장은 60분봉 리샘플링 감시
    interval = "15m" if regime == "BULL" else "60m"

    try:
        df = yf.Ticker(ticker_symbol).history(period="5d", interval=interval)
        if len(df) < 15:
            return

        df["RSI"] = calculate_rsi(df["Close"])
        current_rsi = round(df["RSI"].iloc[-1], 2)
        current_price = round(df["Close"].iloc[-1], 2)

        # RSI 과매도 기준(30 이하) 도달 시 알림
        if current_rsi <= 30:
            msg = f"📈 [{ticker_symbol} RSI 매수 신호 - {regime}장]\n"
            msg += f"• 현재가: ${current_price}\n"
            msg += f"• {interval} RSI: {current_rsi} (과매도 구간 진입)"
            send_telegram_msg(msg)

    except Exception as e:
        print(f"[{ticker_symbol}] RSI 계산 중 오류: {e}")


# ==========================================
# 7. 메인 실행 루틴
# ==========================================
def run_trading_system():
    print("=== 시스템 통합 점검 시작 ===")
    state = load_alert_state()

    # 1. 매크로 지표 조회
    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()
    ma20_up = check_ma20_up("QQQ")

    print(
        f"[매크로 데이터] 공탐지수: {current_fg} | VIX: {current_vix} | QQQ 20일선 우상향: {ma20_up}"
    )

    # 2. 공탐지수 & VIX 전지점/마디가 전용 알림 실행
    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)
    save_alert_state(state)

    # 3. 시장 국면 판정
    regime = get_market_regime(ma20_up, current_fg, current_vix)
    print(f"[현재 시장 국면] {regime}")

    # 4. 자산별(QQQ, SOXX 등) RSI 감시 수행
    for asset in TARGET_ASSETS:
        check_asset_rsi_alert(asset, regime)


# ==========================================
# 8. 백그라운드 무한 루프
# ==========================================
if __name__ == "__main__":
    print("=== 트레이딩 알림 봇 24시간 가동 시작 ===")

    # 서버 재시작 시 최초 1회 가동 알림 전송
    send_telegram_msg(
        "🚀 [알림 봇 가동 완료]\nRender 서버 연결 완료. QQQ/SOXX 감시 및 마크로 스위치를 시작합니다."
    )

    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"실행 중 에러 발생: {e}")

        print("15분 대기 후 다음 모니터링을 진행합니다...\n")
        time.sleep(900)  # 15분 마다 반복
