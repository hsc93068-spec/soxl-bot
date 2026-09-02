import os
from threading import Thread
from flask import Flask

# Render 포트 감지용 가짜 웹서버
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# 백그라운드로 웹서버 실행
Thread(target=run_flask).start()

# --- 이 밑으로는 기존 while True 코드 유지 ---

# ==========================================
# VIX 마디가 알림 보완 버전 (17 초과 첫 알림 / 18, 19, 20, 21 발송)
# ==========================================
def check_vix_alert(current_vix, state):
    if current_vix is None:
        return state

    last_vix_level = state.get("last_vix_level", 0)

    # 1. VIX 17.0 이하: 안심 구간 (리셋)
    if current_vix <= 17.0:
        if last_vix_level != 0:
            state["last_vix_level"] = 0
        return state

    # 2. VIX 22.0 이상: 극단 위험 구간 (상태만 22로 유지)
    if current_vix >= 22.0:
        state["last_vix_level"] = 22
        return state

    # 3. 17.0 초과 ~ 22.0 미만 구간 정수 마디가 판정 (17, 18, 19, 20, 21)
    current_level = int(current_vix)

    # 이전 알림 레벨과 다를 때만 발송
    if current_level != last_vix_level:
        msg = f"⚠️ [VIX 변동성 마디가 알림]\n"
        msg += f"• 현재 VIX: {current_vix:.2f} (마디가: {current_level}pt대)\n\n"

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
import time

# ... (기존 함수들 모두 그대로 유지) ...

if __name__ == "__main__":
    print("=== 트레이딩 알림 봇 24시간 가동 시작 ===")
    
    while True:
        try:
            # 1. 봇 메인 로직 1회 실행
            run_trading_system()
        except Exception as e:
            print(f"실행 중 에러 발생: {e}")
        
        # 2. 15분(900초) 대기 후 다시 루프 돌기 (원하는 주기로 초 단위 변경)
        print("15분 대기 후 다음 체크를 진행합니다...\n")
        time.sleep(900)
