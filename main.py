import json
import os
from threading import Thread
import time
from flask import Flask
import requests
import yfinance as yf

# ==========================================
# 1. Render 웹서버 (포트 감지용)
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
# 2. 기본 설정 및 텔레그램 함수
# ==========================================
STATE_FILE = "trading_bot_state.json"

# ⚠️ 본인의 실제 텔레그램 토큰과 CHAT ID를 입력하세요.
TELEGRAM_TOKEN = "8986570820:AAFfJht2Y02m21_T7SOvSfssOnPozDPTSpg"
CHAT_ID = "1157818555"

TARGET_ASSETS = ["QQQ", "SOXX", "DIA", "VOO", "BTC-USD"]


def send_telegram_msg(message):
    print(f"[텔레그램 발송 시도]\n{message}\n")
    # 토큰이 기본값이 아니고 실제 입력되어 있을 때만 발송
    if TELEGRAM_TOKEN and TELEGRAM_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN":
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            res = requests.post(
                url, data={"chat_id": CHAT_ID, "text": message}, timeout=10
            )
            print(f"[텔레그램 응답] Status: {res.status_code}")
        except Exception as e:
            print(f"텔레그램 발송 오류: {e}")
    else:
        print(
            "⚠️ TELEGRAM_TOKEN 또는 CHAT_ID가 설정되지 않아 발송을 스킵합니다."
        )


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
        print(f"상태 저장 오류: {e}")


# ==========================================
# 3. 데이터 수집 (CNN F&G, VIX, 20일선)
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
        if vix.empty:
            return None
        return round(vix["Close"].iloc[-1], 2)
    except Exception as e:
        print(f"[오류] VIX 수집 실패: {e}")
        return None


def check_ma20_status(symbol="QQQ"):
    """QQQ 일봉 기준 20일선 위에 있고 20일선이 우상향하는지 확인"""
    try:
        df = yf.Ticker(symbol).history(period="3m", interval="1d")
        if df.empty or len(df) < 20:
            return False, False
        df["MA20"] = df["Close"].rolling(20).mean()

        is_above = df["Close"].iloc[-1] > df["MA20"].iloc[-1]
        is_upward = df["MA20"].iloc[-1] > df["MA20"].iloc[-2]
        return is_above, is_upward
    except Exception as e:
        print(f"[오류] MA20 수집 실패: {e}")
        return False, False


# ==========================================
# 4. 공탐지수 & VIX 마디가 전용 알림
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
# 5. 시장 국면 판정 로직
# ==========================================
def get_market_regime(is_above, is_upward, fg_score, vix_score):
    ma20_cond = is_above and is_upward

    # 하락장 (1가지라도 충족 시 즉시 매수 금지 하락장)
    if (
        (not ma20_cond)
        or (fg_score is not None and fg_score <= 40)
        or (vix_score is not None and vix_score >= 20)
    ):
        return "BEAR"

    # 상승장 (3가지 모두 충족 시)
    cond_fg_bull = (fg_score >= 60) if fg_score is not None else False
    cond_vix_bull = (vix_score <= 17) if vix_score is not None else False
    if ma20_cond and cond_fg_bull and cond_vix_bull:
        return "BULL"

    # 횡보장 (3가지 중 2가지 이상 충족 시)
    cond_fg_range = (fg_score >= 50) if fg_score is not None else False
    cond_vix_range = (vix_score <= 20) if vix_score is not None else False

    score = int(ma20_cond) + int(cond_fg_range) + int(cond_vix_range)
    if score >= 2:
        return "RANGE"

    return "BEAR"


# ==========================================
# 6. 알람 1, 2, 3 세부 RSI 반등 계산 및 필터
# ==========================================
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def process_rsi_rule(
    ticker,
    interval_name,
    timeframe,
    threshold_rsi,
    bounce_pt,
    noise_limit,
    dup_limit,
    state,
):
    try:
        # 실시간 진행 봉 포함하여 최근 봉 데이터 수집
        df = yf.Ticker(ticker).history(period="5d", interval=timeframe)
        if df.empty or len(df) < 20:
            return state

        df["RSI"] = calculate_rsi(df["Close"])
        current_rsi = round(df["RSI"].iloc[-1], 2)
        current_price = round(df["Close"].iloc[-1], 2)

        # 최근 10개 봉 중 RSI 최저점 검색
        recent_rsi_min = df["RSI"].tail(10).min()

        # 1. 기준 RSI 이하 진입 이력이 있는지 확인
        if recent_rsi_min <= threshold_rsi:
            bounce = current_rsi - recent_rsi_min

            # 2. 반등 조건 달성 & 최저점 대비 10pt 이상 과도한 반등 차단
            if bounce >= bounce_pt and bounce < noise_limit:
                history_key = f"{ticker}_{interval_name}"
                history = state["rsi_history"].get(history_key, {})

                last_alert_time = history.get("time", 0)
                last_min_rsi = history.get("min_rsi", -999)

                now = time.time()
                time_passed = (now - last_alert_time) / 60  # 분 단위

                # 3. 최근 60분 이내 중복 방지 필터링
                if last_alert_time > 0 and time_passed <= 60:
                    if abs(recent_rsi_min - last_min_rsi) <= dup_limit:
                        return state  # 중복 조건 걸려서 스키프

                # 알림 메시지 생성 및 발송
                msg = f"🔔 [{ticker} RSI 반등 알림 ({interval_name})]\n"
                msg += f"• 현재가: ${current_price}\n"
                msg += f"• 현재 RSI: {current_rsi}pt\n"
                msg += f"• 구간 최저 RSI: {round(recent_rsi_min, 2)}pt (반등: +{round(bounce, 2)}pt)"

                send_telegram_msg(msg)

                # 상태 업데이트
                state["rsi_history"][history_key] = {
                    "time": now,
                    "min_rsi": recent_rsi_min,
                }

    except Exception as e:
        print(f"[{ticker}] {interval_name} RSI 계산 오류: {e}")

    return state


def check_all_asset_rsi_alerts(regime, state):
    # 하락장(BEAR)일 경우 모든 일반 RSI 알림 일체 무시
    if regime == "BEAR":
        return state

    for ticker in TARGET_ASSETS:
        # 상승장일 때: 알람1 (15분봉) 가동
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

        # 횡보장일 때: 알람2 (30분봉) 및 알람3 (60분봉) 가동
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

    return state


# ==========================================
# 7. 통합 메인 실행 루틴
# ==========================================
def run_trading_system():
    print("=== 매매 및 마크로 스위치 점검 시작 ===")
    state = load_state()

    # 1. 지표 데이터 수집
    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()
    is_above, is_upward = check_ma20_status("QQQ")

    print(
        f"[지표 분석] 공탐지수: {current_fg}pt | VIX: {current_vix} | 20일선 위: {is_above}, 우상향: {is_upward}"
    )

    # 2. 공탐지수 / VIX 마디가 전용 알림
    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)

    # 3. 시장 국면 판정 (BULL / RANGE / BEAR)
    regime = get_market_regime(is_above, is_upward, current_fg, current_vix)
    print(f"[현재 시장 국면] {regime}")

    # 4. 개별 자산 RSI 알림 규칙 검사 (하락장 시 자동 차단)
    state = check_all_asset_rsi_alerts(regime, state)

    # 상태 저장
    save_state(state)


# ==========================================
# 8. 백그라운드 무한 루프 실행
# ==========================================
if __name__ == "__main__":
    print("=== 알림 봇 가동 시작 ===")

    # 서버 부팅 즉시 무조건 시작 알림 1회 전송
    send_telegram_msg(
        "🚀 [알림 봇 재가동 완료]\nRender 서버 연결 성공. 알람 1/2/3 규칙 및 마크로 스위치가 통합 탑재되었습니다."
    )

    while True:
        try:
            run_trading_system()
        except Exception as e:
            print(f"메인 루프 에러: {e}")

        time.sleep(900)  # 15분마다 루프
