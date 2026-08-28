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

# 중복 알림 방지용 (이미 알림을 보낸 최저 RSI의 시간을 기록)
last_notified_min_time = {"SOXL": None, "NQ=F": None}

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
    
    # Wilder's Smoothing (alpha = 1/14)
    avg_gain = gain.ewm(alpha=1/window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/window, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_symbol(ticker, name):
    global last_notified_min_time

    # 데이터 기간을 1개월(1mo)로 늘려 차트와 RSI 계산 정밀도 일치시킴
    df = yf.download(tickers=ticker, period="1mo", interval="15m", prepost=True, progress=False)
    
    if df.empty or len(df) < 50:
        print(f"[{name}({ticker})] 데이터를 불러오지 못했습니다.")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df = df.xs(ticker, level=1, axis=1)

    df['RSI'] = calculate_rsi(df)
    df['MA20'] = df['Close'].rolling(window=20).mean()
    
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('Asia/Seoul')
    else:
        df.index = df.index.tz_convert('Asia/Seoul')

    latest_price = float(df['Close'].iloc[-1])
    latest_rsi = float(df['RSI'].iloc[-1])
    latest_ma20 = float(df['MA20'].iloc[-1])
    latest_time = df.index[-1].strftime('%Y-%m-%d %H:%M:%S')

    prev_price = float(df['Close'].iloc[-2])
    prev_ma20 = float(df['MA20'].iloc[-2])

    print(f"[{latest_time} KST] {name}({ticker}) 현재가: ${latest_price:.2f} | RSI: {latest_rsi:.2f} | 20이평: ${latest_ma20:.2f}")

    # 최근 10개 봉(2시간 30분) 이내 데이터만 확인
    recent_df = df.tail(10)
    rsi_under_35 = recent_df[recent_df['RSI'] <= 35]

    if not rsi_under_35.empty:
        # 최근 10개 봉 중 RSI 최저점 지점 추출
        min_rsi_idx = rsi_under_35['RSI'].idxmin()
        min_rsi = float(rsi_under_35['RSI'].loc[min_rsi_idx])
        rsi_diff = latest_rsi - min_rsi
        
        # 조건: 반등폭이 +2pt 이상 ~ +10pt 미만 & 해당 바닥(최저점 시각)으로 이미 알림을 보내지 않았을 때만 발송
        if 2.0 <= rsi_diff < 10.0:
            if last_notified_min_time[name] != min_rsi_idx:
                last_notified_min_time[name] = min_rsi_idx
                msg = (f"📈 [{name} 15분봉 RSI 바닥 반등 신호]\n"
                       f"시간: {latest_time} (KST)\n"
                       f"현재가: ${latest_price:.2f}\n"
                       f"최저 RSI: {min_rsi:.2f} ➔ 현재 RSI: {latest_rsi:.2f} (+{rsi_diff:.2f}pt 상승)\n\n"
                       f"RSI 35 이하 바닥 형성 후 적정 범위(+2pt~+10pt) 내에서 반등 중입니다.")
                send_telegram(msg)

    # 조건 2: 15분봉 20이평선 하향 돌파 알림
    if prev_price >= prev_ma20 and latest_price < latest_ma20:
        msg = (f"📉 [{name} 15분봉 20이평선 하향 돌파]\n"
               f"시간: {latest_time} (KST)\n"
               f"직전가: ${prev_price:.2f} ➔ 현재가: ${latest_price:.2f}\n"
               f"20봉이평선: ${latest_ma20:.2f}")
        send_telegram(msg)

def bot_loop():
    """백그라운드에서 15분마다 감시하는 함수"""
    targets = [("SOXL", "SOXL"), ("NQ=F", "나스닥100 선물")]
    print("🚀 Render에서 SOXL / 나스닥100 선물 감시 봇을 시작합니다 (15분 주기)...")
    send_telegram("🚀 [RSI 정밀도 보정 + 중복 알림 방지] 감시 봇이 시작되었습니다!")
    
    while True:
        try:
            for ticker, name in targets:
                check_symbol(ticker, name)
        except Exception as e:
            print(f"감시 중 에러 발생: {e}")
        
        # 15분(900초) 대기
        time.sleep(900)

if __name__ == "__main__":
    # 봇 감시 로직을 별도 스레드로 실행
    threading.Thread(target=bot_loop, daemon=True).start()
    
    # Render 요구 포트 웹 서버 실행
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
