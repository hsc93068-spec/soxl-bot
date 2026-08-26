import os
import requests
import yfinance as yf
import pandas as pd

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"메시지 전송 실패: {e}")

def calculate_rsi(data, window=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_rsi():
    ticker = "SOXL"
    # 최근 5일 15분봉 데이터 다운로드
    df = yf.download(tickers=ticker, period="5d", interval="15m", progress=False)
    
    if df.empty or len(df) < 15:
        print("데이터를 불러오지 못했습니다.")
        return

    df['RSI'] = calculate_rsi(df)
    
    latest_price = float(df['Close'].iloc[-1])
    latest_rsi = float(df['RSI'].iloc[-1])
    latest_time = df.index[-1].strftime('%Y-%m-%d %H:%M:%S')

    print(f"[{latest_time}] {ticker} 현재가: ${latest_price:.2f} | 15분봉 RSI: {latest_rsi:.2f}")

    # RSI가 30 이하일 때 텔레그램 알림 전송
    if latest_rsi <= 30:
        msg = f"🚨 [SOXL 매수 신호 알림]\n\n시간: {latest_time}\n현재가: ${latest_price:.2f}\nRSI(15분봉): {latest_rsi:.2f}\n\nRSI가 30 이하로 내려갔습니다!"
        send_telegram(msg)
    else:
        print("RSI 30 초과 상태 (알림 미전송)")

if __name__ == "__main__":
    check_rsi()
