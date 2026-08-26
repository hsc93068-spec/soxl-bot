import os
import requests
import yfinance as yf
import pandas as pd

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram(message):
    """텔레그램 메시지 전송 함수"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"메시지 전송 실패: {e}")

def calculate_rsi(data, window=14):
    """RSI(상대강도지수) 계산 함수"""
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_rsi():
    ticker = "SOXL"
    
    # prepost=True: 프리마켓/아프터마켓 포함 전체 시간대 데이터 가져오기
    df = yf.download(tickers=ticker, period="5d", interval="15m", prepost=True, progress=False)
    
    if df.empty or len(df) < 20:
        print("데이터를 불러오지 못했습니다.")
        return

    # RSI 및 20봉 이동평균선 계산
    df['RSI'] = calculate_rsi(df)
    df['MA20'] = df['Close'].rolling(window=20).mean()
    
    # 한국 시간(Asia/Seoul)으로 타임존 변환
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('Asia/Seoul')
    else:
        df.index = df.index.tz_convert('Asia/Seoul')

    latest_price = float(df['Close'].iloc[-1])
    latest_rsi = float(df['RSI'].iloc[-1])
    latest_ma20 = float(df['MA20'].iloc[-1])
    latest_time = df.index[-1].strftime('%Y-%m-%d %H:%M:%S')

    print(f"[{latest_time} KST] {ticker} 현재가: ${latest_price:.2f} | RSI: {latest_rsi:.2f} | 20봉이평선: ${latest_ma20:.2f}")

    # 조건 1: RSI 30 이하 알림
    if latest_rsi <= 30:
        msg = f"🚨 [SOXL RSI 매수 신호]\n시간: {latest_time} (KST)\n현재가: ${latest_price:.2f}\nRSI(15분봉): {latest_rsi:.2f}\n\nRSI가 30 이하로 내려갔습니다!"
        send_telegram(msg)
        
    # 조건 2: 현재가가 20봉 이동평균선 아래로 하락 시 알림
    if latest_price < latest_ma20:
        msg = f"📉 [SOXL 이평선 하향 이탈]\n시간: {latest_time} (KST)\n현재가: ${latest_price:.2f}\n20봉이평선: ${latest_ma20:.2f}\n\n현재가가 20봉 이동평균선 아래로 내려갔습니다!"
        send_telegram(msg)

if __name__ == "__main__":
    check_rsi()
