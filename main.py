import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask

# Render 웹 서버 감지용 간단한 Flask 앱
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 동일 타점 중복 알림 방지 (15분 단위 캔들 시작 시간 기준)
last_notified_time = {"SOXL": None, "NQ=F": None}

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("텔레그램 토큰 또는 CHAT_ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"메시지 전송 실패: {e}")

def calculate_rsi(data, window=14):
    """트레이딩뷰 / 증권사 HTS 표준 (Wilder's Smoothing) RSI 계산 방식"""
    delta = data['Close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.ewm(alpha=1/window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/window, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def resample_and_calc_rsi(df_15m, rule, window=14):
    """15분봉 데이터를 기반으로 30분/60분봉 리샘플링 및 실시간 RSI 계산"""
    resampled = df_15m.resample(rule).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    resampled['RSI'] = calculate_rsi(resampled, window)
    return resampled

def check_symbol(ticker, name):
    global last_notified_time

    # 15분봉 데이터 로드 (실시간 진행 봉 포함)
    df_15m = yf.download(tickers=ticker, period="1mo", interval="15m", prepost=True, progress=False)
    
    if df_15m.empty or len(df_15m) < 50:
        print(f"[{name}({ticker})] 데이터를 불러오지 못했습니다.")
        return

    if isinstance(df_15m.columns, pd.MultiIndex):
        df_15m = df_15m.xs(ticker, level=1, axis=1)

    # 15분, 30분, 60분 모두 실시간 진행 봉을 반영하여 계산
    df_15m['RSI'] = calculate_rsi(df_15m)
    df_30m = resample_and_calc_rsi(df_15m, '30min')
    df_60m = resample_and_calc_rsi(df_15m, '60min')

    for df in [df_15m, df_30m, df_60m]:
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Seoul')
        else:
            df.index = df.index.tz_convert('Asia/Seoul')

    latest_price = float(df_15m['Close'].iloc[-1])
    latest_time = df_15m.index[-1].strftime('%Y-%m-%d %H:%M:%S')

    # --- [조건 1] 15분봉(실시간): RSI 30 이하에서 +3pt 이상 반등 ---
    rec_15m = df_15m.tail(10)
    cond1 = False
    rsi_15m_min, rsi_15m_now, rsi_15m_diff = 0.0, 0.0, 0.0
    under_30 = rec_15m[rec_15m['RSI'] <= 30]
    if not under_30.empty:
        rsi_15m_min = float(under_30['RSI'].min())
        rsi_15m_now = float(df_15m['RSI'].iloc[-1])
        rsi_15m_diff = rsi_15m_now - rsi_15m_min
        if rsi_15m_diff >= 3.0:
            cond1 = True

    # --- [조건 2] 30분봉(실시간): RSI 35 이하에서 +2pt 이상 반등 ---
    rec_30m = df_30m.tail(10)
    cond2 = False
    rsi_30m_min, rsi_30m_now, rsi_30m_diff = 0.0, 0.0, 0.0
    under_35 = rec_30m[rec_30m['RSI'] <= 35]
    if not under_35.empty:
        rsi_30m_min = float(under_35['RSI'].min())
        rsi_30m_now = float(df_30m['RSI'].iloc[-1])
        rsi_30m_diff = rsi_30m_now - rsi_30m_min
        if rsi_30m_diff >= 2.0:
            cond2 = True

    # --- [조건 3] 60분봉(실시간): RSI 40 이하에서 +1pt 이상 반등 ---
    rec_60m = df_60m.tail(10)
    cond3 = False
    rsi_60m_min, rsi_60m_now, rsi_60m_diff = 0.0, 0.0, 0.0
    under_40 = rec_60m[rec_60m['RSI'] <= 40]
    if not under_40.empty:
        rsi_60m_min = float(under_40['RSI'].min())
        rsi_60m_now = float(df_60m['RSI'].iloc[-1])
        rsi_60m_diff = rsi_60m_now - rsi_60m_min
        if rsi_60m_diff >= 1.0:
            cond3 = True

    print(f"[실시간 감시 중] {name} 현재가: ${latest_price:.2f} | 15m RSI: {rsi_15m_now:.1f} | 30m RSI: {rsi_30m_now:.1f} | 60m RSI: {rsi_60m_now:.1f}")

    # --- 조건 1, 2, 3 동시 만족 시에만 알림 발송 ---
    if cond1 and cond2 and cond3:
        if last_notified_time[name] != latest_time:
            last_notified_time[name] = latest_time
            msg = (f"🎯 [{name} 15m/30m/60m 정밀 바닥 반등 신호!]\n\n"
                   f"시간: 실시간 진행 봉 기준\n"
                   f"현재가: ${latest_price:.2f}\n\n"
                   f"1️⃣ 15분봉(실시간): 최저 {rsi_15m_min:.1f} ➔ 현재 {rsi_15m_now:.1f} (+{rsi_15m_diff:.1f}pt)\n"
                   f"2️⃣ 30분봉(실시간): 최저 {rsi_30m_min:.1f} ➔ 현재 {rsi_30m_now:.1f} (+{rsi_30m_diff:.1f}pt)\n"
                   f"3️⃣ 60분봉(실시간): 최저 {rsi_60m_min:.1f} ➔ 현재 {rsi_60m_now:.1f} (+{rsi_60m_diff:.1f}pt)\n\n"
                   f"🔥 3개 분봉 실시간 과매도 돌파 및 초입 반등 충족!")
            send_telegram(msg)

def bot_loop():
    """백그라운드에서 매 1분마다 실시간으로 낚아채는 감시 루프"""
    targets = [("SOXL", "SOXL"), ("NQ=F", "나스닥100 선물")]
    print("🚀 멀티 타임프레임 실시간 순간 포착 감시 봇 시작 (1분 주기)...")
    send_telegram("🚀 [15m(+3pt) / 30m(+2pt) / 60m(+1pt) 실시간 감시] 봇이 시작되었습니다!")
    
    while True:
        try:
            for ticker, name in targets:
                check_symbol(ticker, name)
        except Exception as e:
            print(f"감시 중 에러 발생: {e}")
        
        # 1분(60초)마다 실시간 가격 감시
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
