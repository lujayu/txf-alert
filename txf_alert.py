import os
import time
import requests
from collections import deque
from datetime import datetime, timezone, timedelta

# ================= 1. 參數設定區（從 GitHub Secrets 加密讀取） =================
TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

# 規則一：短線急波動（1 分鐘內波動超過 100 點）
SHORT_WINDOW_SEC = 60      # 60 秒（1 分鐘）
SHORT_THRESHOLD = 100      # 觸發門檻：100 點
SHORT_COOLDOWN = 60        # 同方向警報冷卻時間：60 秒

# 規則二：波段大行情（5 分鐘內波動超過 200 點）
LONG_WINDOW_SEC = 300      # 300 秒（5 分鐘）
LONG_THRESHOLD = 200       # 觸發門檻：200 點
LONG_COOLDOWN = 180        # 同方向警報冷卻時間：180 秒（3 分鐘）

POLL_INTERVAL = 3          # 抓取頻率：每 3 秒查詢一次最新報價
RUN_DURATION = 580         # 單次執行秒數：約 9 分 40 秒後交棒給下一班排程

TW_TZ = timezone(timedelta(hours=8))

def get_tw_now():
    return datetime.now(TW_TZ)

# ================= 2. Telegram 推播函式 =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TOKEN.strip()}/sendMessage"
    payload = {
        "chat_id": CHAT_ID.strip(),
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

# ================= 4. 主監控迴圈（雙時間週期並行） =================
def run_monitor():
    # 保留最長 5 分鐘（300 秒）內的歷史報價
    price_history = deque()
    
    # 兩組條件各自獨立的冷卻計時器
    last_alert_short = {"UP": 0, "DOWN": 0}
    last_alert_long = {"UP": 0, "DOWN": 0}
    
    start_ts = time.time()
    start_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🚀 [{start_str} 台灣時間] 雙週期雲端排程監控開始（1分100點 / 5分200點）...", flush=True)
    
    while (time.time() - start_ts) < RUN_DURATION:
        price, contract, diff = get_txf_realtime_price()
        now_ts = time.time()
        now_str = get_tw_now().strftime("%H:%M:%S")
        
        if price is not None:
            price_history.append((now_ts, price))
            # 清除超過 5 分鐘（LONG_WINDOW_SEC）的舊資料
            while price_history and (now_ts - price_history[0][0] > LONG_WINDOW_SEC):
                price_history.popleft()
            
            # --- 計算 1 分鐘內高低點 ---
            short_prices = [p for ts, p in price_history if (now_ts - ts) <= SHORT_WINDOW_SEC]
            s_min, s_max = min(short_prices), max(short_prices)
            s_surge = price - s_min
            s_plunge = s_max - price
            
            # --- 計算 5 分鐘內高低點 ---
            long_prices = [p for _, p in price_history]
            l_min, l_max = min(long_prices), max(long_prices)
            l_surge = price - l_min
            l_plunge = l_max - price
            
            print(
                f"[{now_str}] {contract} 現價: {price:,.0f} | "
                f"1分高低: {s_max:,.0f}/{s_min:,.0f} | 5分高低: {l_max:,.0f}/{l_min:,.0f}",
                flush=True
            )
            
            # ================= 規則一：1 分鐘內波動 >= 100 點 =================
            if s_surge >= SHORT_THRESHOLD and (now_ts - last_alert_short["UP"] > SHORT_COOLDOWN):
                msg = (
                    f"🔥【台指期 1分鐘急漲警報】🔥\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"1 分鐘內急拉：+{s_surge:,.0f} 點（低點 {s_min:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_short["UP"] = now_ts
                
            if s_plunge >= SHORT_THRESHOLD and (now_ts - last_alert_short["DOWN"] > SHORT_COOLDOWN):
                msg = (
                    f"❄️【台指期 1分鐘急跌警報】❄️\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"1 分鐘內急殺：-{s_plunge:,.0f} 點（高點 {s_max:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_short["DOWN"] = now_ts

            # ================= 規則二：5 分鐘內波動 >= 200 點 =================
            if l_surge >= LONG_THRESHOLD and (now_ts - last_alert_long["UP"] > LONG_COOLDOWN):
                msg = (
                    f"🚀🚀【台指期 5分鐘波段暴漲】🚀🚀\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"5 分鐘內大漲：+{l_surge:,.0f} 點（低點 {l_min:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_long["UP"] = now_ts
                
            if l_plunge >= LONG_THRESHOLD and (now_ts - last_alert_long["DOWN"] > LONG_COOLDOWN):
                msg = (
                    f"🌊🌊【台指期 5分鐘波段重挫】🌊🌊\n"
                    f"合約：{contract}\n"
                    f"目前現價：{price:,.0f} 點\n"
                    f"5 分鐘內重挫：-{l_plunge:,.0f} 點（高點 {l_max:,.0f}）\n"
                    f"台灣時間：{now_str}"
                )
                send_telegram_alert(msg)
                last_alert_long["DOWN"] = now_ts
        else:
            print(f"[{now_str}] 目前無最新成交報價（休市或盤前）", flush=True)
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run_monitor()
