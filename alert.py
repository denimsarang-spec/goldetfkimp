import os, re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, time, timezone, timedelta

KST = timezone(timedelta(hours=9))
OZ_TO_G = 31.1034768

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.9",
}

# ====== 장중 체크 ======
def is_korean_market_hours() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:  # 토/일 제외
        return False
    t = now.time()
    return time(9, 0) <= t <= time(15, 30)  # KRX 정규장

# ====== 공통 fetch ======
def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text

def num_from(text: str, pattern: str) -> float:
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        raise ValueError("number not found")
    return float(m.group(1).replace(",", ""))

def fmt_won(x: float) -> str:
    return f"{int(round(x)):,}"

def fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"

# ====== 네이버 (현재가) ======
def get_naver_stock_html(code: str) -> str:
    return fetch(f"https://finance.naver.com/item/main.nhn?code={code}")

def get_naver_current_price(code: str) -> float:
    html = get_naver_stock_html(code)
    m = re.search(r"현재가\s*([0-9]{1,3}(?:,[0-9]{3})*)", html)
    if not m:
        raise ValueError(f"Naver current price not found for {code}")
    return float(m.group(1).replace(",", ""))

# ====== 네이버 지표(환율/국내금/국제금) ======
def get_usdkrw() -> float:
    html = fetch("https://m.stock.naver.com/marketindex/exchange/FX_USDKRW")
    txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return num_from(txt, r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)")

def get_domestic_gold_krw_per_g() -> float:
    html = fetch("https://m.stock.naver.com/marketindex/metals/M04020000")
    txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return num_from(txt, r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*원/g")

def get_international_gold_usd_per_oz() -> float:
    html = fetch("https://m.stock.naver.com/marketindex/metals/GCcv1")
    txt = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return num_from(txt, r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*USD/OZS")

# ====== 텔레그램 ======
def send_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
    r.raise_for_status()

# ====== 보유손익 ======
def pnl_line(name: str, code: str, avg: float, qty: int) -> str:
    cur = get_naver_current_price(code)
    value = cur * qty
    cost = avg * qty
    pl = value - cost
    pl_pct = (cur / avg - 1.0) * 100.0
    return (
        f"- {name}({code}) 현재 {fmt_won(cur)} / 평단 {fmt_won(avg)} x {qty}주"
        f" | 평가 {fmt_won(value)} | 손익 {fmt_won(pl)} ({pl_pct:+.2f}%)"
    )

if __name__ == "__main__":
    # 테스트 스위치 (workflow_dispatch 입력값으로 제어)
    FORCE_RUN = os.environ.get("FORCE_RUN", "0") == "1"        # 장중 체크 무시
    TEST_MESSAGE = os.environ.get("TEST_MESSAGE", "0") == "1"  # 조건 무시(무조건 1회 전송)

    # 장중 아니면 종료 (테스트는 FORCE_RUN=1)
    if (not FORCE_RUN) and (not is_korean_market_hours()):
        raise SystemExit(0)

    TH = float(os.environ.get("KIMCHI_THRESHOLD", "1.5"))

    # 1) 금 김프(국내 vs 국제환산)
    usdkrw = get_usdkrw()
    dom_g = get_domestic_gold_krw_per_g()
    intl_usd_oz = get_international_gold_usd_per_oz()
    intl_krw_g = intl_usd_oz * usdkrw / OZ_TO_G
    kimchi = (dom_g - intl_krw_g) / intl_krw_g * 100.0

    # 2) 알림 조건: "금 김프"만 기준
    if (not TEST_MESSAGE) and (abs(kimchi) < TH):
        raise SystemExit(0)

    # 3) 보유손익
    pnl_411060 = pnl_line("ACE KRX금현물", "411060", 37510, 118)
    pnl_091160 = pnl_line("KODEX반도체", "091160", 85700, 62)

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    msg = "\n".join([
        f"[ALERT] {now}",
        "",
        "■ 금 김프(국내금 vs 국제금 환산)",
        f"- 국제금: {intl_usd_oz:,.2f} USD/oz",
        f"- 환율: {usdkrw:,.2f} KRW/USD",
        f"- 국제금 환산: {intl_krw_g:,.0f} 원/g",
        f"- 국내금: {dom_g:,.0f} 원/g",
        f"- 김프: {fmt_pct(kimchi)}",
        "",
        "■ 보유 손익",
        pnl_411060,
        pnl_091160,
        "",
        f"■ 알림 조건: |금 김프| ≥ {TH:.2f}%",
        f"(FORCE_RUN={int(FORCE_RUN)}, TEST_MESSAGE={int(TEST_MESSAGE)})",
    ])

    send_telegram(msg)
