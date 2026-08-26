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

def check_symbol(ticker, name):
    """종목별 RSI 및 이평선 체크 함수"""
    df = yf.download(tickers=ticker, period="5d", interval="15m", prepost=True, progress=False)
    
    if df.empty or len(df) < 20:
        print(f"[{name}({ticker})] 데이터를 불러오지 못했습니다.")
        return

    if isinstance(df.columns, pd.MultiIndex):
        df = df.xs(ticker, level=1, axis=1)

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

    print(f"[{latest_time} KST] {name}({ticker}) 현재가: ${latest_price:.2f} | RSI: {latest_rsi:.2f} | 20봉이평선: ${latest_ma20:.2f}")

    # 최근 데이터 중에서 RSI가 35 이하였던 구간 감지 및 반등 체크
    # 최근 10개 봉(2시간 30분) 이내에 RSI 35 이하 진입 내역이 있는지 확인
    recent_df = df.tail(10)
    rsi_under_35 = recent_df[recent_df['RSI'] <= 35]

    if not rsi_under_35.empty:
        min_rsi = rsi_under_35['RSI'].min() # RSI 35 이하 구간에서의 최저 RSI 값
        
        # 조건: 현재 RSI가 최저점 대비 4pt 이상 상승했고, 현재 RSI도 여전히 40 이하인 반등 초기 단계
        if latest_rsi >= (min_rsi + 4) and latest_rsi <= 40:
            msg = (f"📈 [{name} RSI 바닥 반등 신호]\n"
                   f"시간: {latest_time} (KST)\n"
                   f"현재가: ${latest_price:.2f}\n"
                   f"최저 RSI: {min_rsi:.2f} ➔ 현재 RSI: {latest_rsi:.2f} (+{latest_rsi - min_rsi:.2f}pt 상승)\n\n"
                   f"RSI 35 이하 바닥 형성 후 +4pt 이상 반등했습니다!")
            send_telegram(msg)

    # 조건 2: 현재가가 20봉 이동평균선 아래로 하락 시 알림
    if latest_price < latest_ma20:
        msg = f"📉 [{name} 이평선 하향 이탈]\n시간: {latest_time} (KST)\n현재가: ${latest_price:.2f}\n20봉이평선: ${latest_ma20:.2f}"
        send_telegram(msg)

if __name__ == "__main__":
    targets = [
        ("SOXL", "SOXL"),
        ("NQ=F", "나스닥100 선물")
    ]
    
    for ticker, name in targets:
        check_symbol(ticker, name)
