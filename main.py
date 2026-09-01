import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "All-in-One Multi-Asset Bot is running!"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 5개 종목(주식 4개 + 비트코인 1개)의 알람별 마지막 '최저점 RSI' 기록
last_notified_min_rsi = {
    "QQQ": {"alarm1": None, "alarm2": None, "alarm3": None},
    "SOXX": {"alarm1": None, "alarm2": None, "alarm3": None},
    "DIA": {"alarm1": None, "alarm2": None, "alarm3": None},
    "VOO": {"alarm1": None, "alarm2": None, "alarm3": None},
    "BTC(비트코인)": {"alarm1": None, "alarm2": None, "alarm3": None}
}

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
    """트레이딩뷰 / HTS 표준 (Wilder's Smoothing) RSI 계산 방식"""
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
    global last_notified_min_rsi

    # 15분봉 데이터 로드 (실시간 진행 중 봉 포함)
    df_15m = yf.download(tickers=ticker, period="1mo", interval="15m", prepost=True, progress=False)
    
    if df_15m.empty or len(df_15m) < 50:
        print(f"[{name}({ticker})] 데이터를 불러오지 못했습니다.")
        return

    if isinstance(df_15m.columns, pd.MultiIndex):
        df_15m = df_15m.xs(ticker, level=1, axis=1)

    # 15분, 30분, 60분 데이터 및 실시간 RSI 계산
    df_15m['RSI'] = calculate_rsi(df_15m)
    df_30m = resample_and_calc_rsi(df_15m, '30min')
    df_60m = resample_and_calc_rsi(df_15m, '60min')

    for df in [df_15m, df_30m, df_60m]:
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Seoul')
        else:
            df.index = df.index.tz_convert('Asia/Seoul')

    latest_price = float(df_15m['Close'].iloc[-1])
    latest_time_str = df_15m.index[-1].strftime('%Y-%m-%d %H:%M:%S')

    # 최근 60분 구간 데이터 분할 (15분봉 4개, 30분봉 2개, 60분봉 1개)
    recent_60m_15m = df_15m.tail(4)
    recent_60m_30m = df_30m.tail(2)
    recent_60m_60m = df_60m.tail(1)

    # 자산별 과매도 기준 설정 (비트코인은 변동성이 커서 30/32/34, 주식은 30/33/36)
    if "BTC" in ticker:
        rsi_th_15m, rsi_th_30m, rsi_th_60m = 30.0, 32.0, 34.0
    else:
        rsi_th_15m, rsi_th_30m, rsi_th_60m = 30.0, 33.0, 36.0

    # ==========================================
    # 🚨 [알람 1] 15분봉: +4pt 이상 ~ +10pt 미만 반등
    # 차단 필터: 최근 60분 최저점 RSI < 마지막 알림 최저점 RSI (엄격한 신저점 경신)
    # ==========================================
    under_15m = recent_60m_15m[recent_60m_15m['RSI'] <= rsi_th_15m]
    if not under_15m.empty:
        rsi_min_60m = float(under_15m['RSI'].min())
        rsi_now = float(df_15m['RSI'].iloc[-1])
        rsi_diff = rsi_now - rsi_min_60m
        
        if 4.0 <= rsi_diff < 10.0:
            last_min = last_notified_min_rsi[name]["alarm1"]
            if last_min is None or rsi_min_60m < last_min:
                last_notified_min_rsi[name]["alarm1"] = rsi_min_60m
                msg = (f"🚨 [알람1 - 15분봉 바닥 반등] {name}\n\n"
                       f"시간: 실시간 진행 봉 ({latest_time_str} KST)\n"
                       f"현재가: ${latest_price:,.2f}\n"
                       f"15m RSI: 최저 {rsi_min_60m:.1f} ➔ 현재 {rsi_now:.1f} (+{rsi_diff:.1f}pt)\n\n"
                       f"👉 조건: RSI ≤ {rsi_th_15m:.0f} 진입 후 +4pt~+10pt 미만 반등 충족\n"
                       f"📉 이전 최저점 대비 엄격한 신저점 경신 확인")
                send_telegram(msg)

    # ==========================================
    # 🚨 [알람 2] 30분봉: +3pt 이상 ~ +10pt 미만 반등
    # ==========================================
    under_30m = recent_60m_30m[recent_60m_30m['RSI'] <= rsi_th_30m]
    if not under_30m.empty:
        rsi_min_60m = float(under_30m['RSI'].min())
        rsi_now = float(df_30m['RSI'].iloc[-1])
        rsi_diff = rsi_now - rsi_min_60m
        
        if 3.0 <= rsi_diff < 10.0:
            last_min = last_notified_min_rsi[name]["alarm2"]
            if last_min is None or rsi_min_60m < last_min:
                last_notified_min_rsi[name]["alarm2"] = rsi_min_60m
                msg = (f"🚨 [알람2 - 30분봉 바닥 반등] {name}\n\n"
                       f"시간: 실시간 진행 봉 ({latest_time_str} KST)\n"
                       f"현재가: ${latest_price:,.2f}\n"
                       f"30m RSI: 최저 {rsi_min_60m:.1f} ➔ 현재 {rsi_now:.1f} (+{rsi_diff:.1f}pt)\n\n"
                       f"👉 조건: RSI ≤ {rsi_th_30m:.0f} 진입 후 +3pt~+10pt 미만 반등 충족\n"
                       f"📉 이전 최저점 대비 엄격한 신저점 경신 확인")
                send_telegram(msg)

    # ==========================================
    # 🚨 [알람 3] 60분봉: +2pt 이상 ~ +10pt 미만 반등
    # ==========================================
    under_60m = recent_60m_60m[recent_60m_60m['RSI'] <= rsi_th_60m]
    if not under_60m.empty:
        rsi_min_60m = float(under_60m['RSI'].min())
        rsi_now = float(df_60m['RSI'].iloc[-1])
        rsi_diff = rsi_now - rsi_min_60m
        
        if 2.0 <= rsi_diff < 10.0:
            last_min = last_notified_min_rsi[name]["alarm3"]
            if last_min is None or rsi_min_60m < last_min:
                last_notified_min_rsi[name]["alarm3"] = rsi_min_60m
                msg = (f"🚨 [알람3 - 60분봉 바닥 반등] {name}\n\n"
                       f"시간: 실시간 진행 봉 ({latest_time_str} KST)\n"
                       f"현재가: ${latest_price:,.2f}\n"
                       f"60m RSI: 최저 {rsi_min_60m:.1f} ➔ 현재 {rsi_now:.1f} (+{rsi_diff:.1f}pt)\n\n"
                       f"👉 조건: RSI ≤ {rsi_th_60m:.0f} 진입 후 +2pt~+10pt 미만 반등 충족\n"
                       f"📉 이전 최저점 대비 엄격한 신저점 경신 확인")
                send_telegram(msg)

    rsi_15m_c = float(df_15m['RSI'].iloc[-1])
    rsi_30m_c = float(df_30m['RSI'].iloc[-1])
    rsi_60m_c = float(df_60m['RSI'].iloc[-1])
    print(f"[실시간 감시 중] {name} 현재가: ${latest_price:,.2f} | 15m RSI: {rsi_15m_c:.1f} | 30m RSI: {rsi_30m_c:.1f} | 60m RSI: {rsi_60m_c:.1f}")

def bot_loop():
    """1분 주기 통합 감시 루프 (미국주식 4종목 + 비트코인)"""
    targets = [
        ("QQQ", "QQQ"),
        ("SOXX", "SOXX"),
        ("DIA", "DIA"),
        ("VOO", "VOO"),
        ("BTC-USD", "BTC(비트코인)")
    ]
    print("🚀 [주식 4종목 + 비트코인] 통합 실시간 감시 봇 시작 (1분 주기)...")
    send_telegram("🚀 [QQQ / SOXX / DIA / VOO / BTC-USD] 5개 자산 통합 실시간 감시 봇이 시작되었습니다!")
    
    while True:
        try:
            for ticker, name in targets:
                check_symbol(ticker, name)
        except Exception as e:
            print(f"감시 중 에러 발생: {e}")
        
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
