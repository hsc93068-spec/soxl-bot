import json
import os
import requests

# ==========================================
# 1. 상태 저장 파일 및 설정
# ==========================================
STATE_FILE = "macro_alert_state.json"
# TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
# CHAT_ID = "YOUR_CHAT_ID"


def send_telegram_msg(message):
    print(f"[텔레그램 발송]\n{message}\n")
    # requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": message})


# ==========================================
# 2. 데이터 수집 모듈 (공탐지수 & VIX)
# ==========================================
def get_fear_and_greed():
    """CNN Fear & Greed API에서 점수 수집"""
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return round(res.json()["fear_and_greed"]["score"])
    except Exception as e:
        print(f"[오류] 공탐지수 수집 실패: {e}")
        return None


def get_vix_index():
    """VIX 수집 (yfinance 또는 API 연동 예시)"""
    try:
        # import yfinance as yf
        # vix_data = yf.Ticker("^VIX").history(period="1d")
        # return round(vix_data["Close"].iloc[-1], 2)
        return 18.4  # 실전 연동 시 실제 수치 반환
    except Exception as e:
        print(f"[오류] VIX 수집 실패: {e}")
        return None


# ==========================================
# 3. 상태 관리 (JSON)
# ==========================================
def load_alert_state():
    default_state = {
        "last_fg_score": 100,
        "last_vix_level": 0,  # 0: 17이하, 17: 첫진입, 18~21: 각 마디가
    }
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
# 4. 공탐지수 전저점 돌파 알림 (60 이하, 5pt 단위)
# ==========================================
def check_fear_and_greed_alert(current_fg, state):
    if current_fg is None:
        return state

    last_fg = state.get("last_fg_score", 100)

    # 60 초과로 복귀 시 리셋
    if current_fg > 60:
        state["last_fg_score"] = 100
        return state

    # 60 이하이면서 이전 알림 대비 5pt 이상 하락 시
    if current_fg <= 60 and (last_fg - current_fg) >= 5:
        target_step = (current_fg // 5) * 5

        msg = f"🚨 [공포지수 하향 돌파 알림]\n"
        msg += f"• 현재 공탐지수: {current_fg}pt (기준 단계: {target_step}pt 이하)\n\n"

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


# ==========================================
# 5. VIX 마디가 알림 (17 초과 첫 알림 / 18, 19, 20, 21만 발송)
# ==========================================
def check_vix_alert(current_vix, state):
    if current_vix is None:
        return state

    last_vix_level = state.get("last_vix_level", 0)

    # 1. VIX 17 이하: 안심 구간 (리셋 및 알림 무시)
    if current_vix <= 17.0:
        state["last_vix_level"] = 0
        return state

    # 2. VIX 22 이상: 극단 위험 구간 (알림 안 보내고 상태만 22로 유지)
    if current_vix >= 22.0:
        state["last_vix_level"] = 22
        return state

    # 3. VIX 17 초과 ~ 22 미만 구간의 마디가 계산 (17, 18, 19, 20, 21)
    current_level = int(current_vix)  # 예: 18.4 -> 18, 17.8 -> 17

    # 이전 마디가 레벨과 달라졌을 때만 알림 발생
    if current_level != last_vix_level:
        msg = f"⚠️ [VIX 변동성 마디가 알림]\n"
        msg += f"• 현재 VIX: {current_vix:.2f} (마디가: {current_level})\n\n"

        if current_level == 17 and last_vix_level == 0:
            msg += "📢 VIX 17.0 초과 진입! (상승장 경계선 이탈, 변동성 주의)"
        elif current_level == 18:
            msg += "📢 VIX 18pt 도달 (횡보/변동성 확대 구간)"
        elif current_level == 19:
            msg += "⚠️ VIX 19pt 도달 (시장 경계 심화)"
        elif current_level == 20:
            msg += "🚨 VIX 20pt 도달 (하락장 경계선 진입 - RSI 매수 금지 가동)"
        elif current_level == 21:
            msg += "🚨 VIX 21pt 도달 (공포 심화, 추가 폭락 주의)"

        send_telegram_msg(msg)
        state["last_vix_level"] = current_level

    return state


# ==========================================
# 6. 시장 국면 판정 로직
# ==========================================
def get_market_regime(ma20_up, fg_score, vix_score):
    """
    - 상승장: 20일선 우상향 AND 공탐지수 >= 60 AND VIX <= 17 (3가지 모두 충족)
    - 횡보장: 3가지 중 2가지 이상 충족
    - 하락장: 20일선 역배열 OR 공탐지수 <= 40 OR VIX >= 20 (1가지라도 충족)
    """
    if (
        not ma20_up
        or (fg_score and fg_score <= 40)
        or (vix_score and vix_score >= 20)
    ):
        return "BEAR"

    cond_ma = ma20_up
    cond_fg = fg_score >= 60 if fg_score else False
    cond_vix = vix_score <= 17 if vix_score else False

    if cond_ma and cond_fg and cond_vix:
        return "BULL"

    return "RANGE"


# ==========================================
# 7. 메인 실행 루틴 (Main Loop)
# ==========================================
def run_trading_system():
    print("=== 시스템 통합 점검 시작 ===")
    state = load_alert_state()

    # 1. 지표 수집
    current_fg = get_fear_and_greed()
    current_vix = get_vix_index()
    ma20_up = False  # 기존 20일선 상태 수집 함수 연동

    print(f"[현재 상태] 공탐지수: {current_fg}pt | VIX: {current_vix}")

    # 2. 공탐지수 및 VIX 전용 마디가 알림 검사
    state = check_fear_and_greed_alert(current_fg, state)
    state = check_vix_alert(current_vix, state)
    save_alert_state(state)

    # 3. 시장 국면 판정
    regime = get_market_regime(ma20_up, current_fg, current_vix)
    print(f"[시장 국면] {regime}")

    # 4. 5개 자산(QQQ, SOXX, DIA, VOO, BTC-USD) RSI 알림 검사
    target_assets = ["QQQ", "SOXX", "DIA", "VOO", "BTC-USD"]
    for asset in target_assets:
        # 기존 check_rsi_alerts(asset, regime) 호출
        pass


if __name__ == "__main__":
    run_trading_system()
