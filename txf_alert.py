import time
import requests
from collections import deque
from datetime import datetime, timezone, timedelta

# ================= 1. 參數設定區 =================
TOKEN = "8324825861:AAFFlo8-B_6kB6cIbhvrVakQI5QpWqc6eRI"
CHAT_ID = "9216134050"

THRESHOLD_POINTS = 100   # 觸發門檻：1 分鐘內波動超過 100 點
WINDOW_SECONDS = 60      # 監控時間窗口：60 秒（1 分鐘）
COOLDOWN_SECONDS = 60    # 警報冷卻時間：觸發後 60 秒內不重複發送同方向警報
POLL_INTERVAL = 3        # 抓取頻率：每 3 秒查詢一次最新報價
RUN_DURATION = 580       # 單次執行秒數：約 9 分 40 秒後交棒給下一班排程

TW_TZ = timezone(timedelta(hours=8))

def get_tw_now():
    return datetime.now(TW_TZ)

# ================= 2. Telegram 推播函式 =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TOKEN.strip()}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }
    try:
        res = requests.post(url, data=payload, timeout=5)
        print(f"Telegram 回應: {res.status_code}", flush=True)
    except Exception as e:
        print(f"⚠️ Telegram 發送失敗：{e}", flush=True)

# ================= 3. 抓取期交所即時台指期報價 =================
def get_txf_realtime_price():
    url = "https://mis.taifex.com.tw/futures/api/getQuoteList"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Referer": "https://mis.taifex.com.tw/futures/"
    }
    
    now = get_tw_now()
    current_hm = now.hour * 100 + now.minute
    # 14:45 ~ 次日 05:15 查詢夜盤（"1"），其餘為日盤（"0"）
    market_type = "1" if (current_hm >= 1445 or current_hm <= 515) else "0"
    
    payload = {
        "MarketType": market_type,
        "SymbolType": "F",
        "KindID": "1",
        "CID": "TXF",
        "ExpireMonth": "",
        "RowSize": "全部",
        "PageNo": "",
        "SortColumn": "",
        "AscDesc": "A"
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        data = res.json()
        quote_list = data.get("RtData", {}).get("QuoteList", [])
        
        for item in quote_list:
            symbol_id = item.get("SymbolID", "")
            last_price_str = item.get("CLastPrice", "")
            if "/" not in symbol_id and last_price_str not in ("", "-", None):
                return float(last_price_str), symbol_id, item.get("CDiff", "0")
    except Exception as e:
        print(f"⚠️ 抓取報價異常：{e}", flush=True)
        
    return None, None, None

# ================= 4. 主監控迴圈 =================
def run_monitor():
    price_history = deque()
    last_alert_time = {"UP": 0, "DOWN": 0}
    start_ts = time.time()
    
    start_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🚀 [{start_str} 台灣時間] 雲端排程監控開始...", flush=True)
    
    while (time.time() - start_ts) < RUN_DURATION:
        price, contract, diff = get_txf_realtime_price()
        now_ts = time.time()
        now_str = get_tw_now().strftime("%H:%M:%S")
        
        if price is not None:
            price_history.append((now_ts, price))
            while price_history and (now_ts - price_history[0][0] > WINDOW_SECONDS):
                price_history.popleft()
            
            prices_in_window = [p for _, p in price_history]
            min_price = min(prices_in_window)
            max_price = max(prices_in_window)
            
            surge = price - min_price
            plunge = max_price - price
            
            print(f"[{now_str}] {contract} 現價: {price:,.0f} | 1分內高低: {max_price:,.0f} / {min_price:,.0f}", flush=True)
            
            # 急漲超過門檻
            if surge >= THRESHOLD_POINTS and (now_ts - last_alert_time["UP"] > COOLDOWN_SECONDS):
                msg = (
                    f"🔥【台指期急漲警報】🔥\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"1 分鐘內急拉：+{surge:,.0f} 點（低點 {min_price:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_time["UP"] = now_ts
                
            # 急跌超過門檻
            if plunge >= THRESHOLD_POINTS and (now_ts - last_alert_time["DOWN"] > COOLDOWN_SECONDS):
                msg = (
                    f"❄️【台指期急跌警報】❄️\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"1 分鐘內急殺：-{plunge:,.0f} 點（高點 {max_price:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_time["DOWN"] = now_ts
        else:
            print(f"[{now_str}] 目前無最新成交報價（休市或盤前）", flush=True)
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run_monitor()
