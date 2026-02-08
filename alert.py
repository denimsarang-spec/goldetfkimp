import os, re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, time, timezone, timedelta

KST = timezone(timedelta(hours=9))
OZ_TO_G = 31.1034768

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}

def is_korean_market_hours() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(9, 0) <= t <= time(15, 30)

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text

def num_from(text: str, pattern: str) -> float:
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        raise ValueError("number not found")
    return float(m.group(1).replace(",", ""))

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

def get_ace_411060_price_and_nav() -> tuple[float, float]:
    url = "https://www.aceetf.co.kr/fund/K55101DN7441"
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    # 1) meta description(서버에서 내려오는 요약문) 우선 파싱
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta:
        meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        desc = meta["content"]
        # 예: "현재가: 33,020원 ; 기준가(NAV)-...: 32,919.41원 ..."
        m_px = re.search(r"현재가[^0-9]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*원", desc)
        nav_list = re.findall(r"기준가\(NAV\)[^0-9]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*원", desc)
        if m_px and nav_list:
            price = float(m_px.group(1).replace(",", ""))
            nav = float(nav_list[-1].replace(",", ""))
            return price, nav

    # 2) meta가 없거나 실패하면 HTML 전체에서 넓게 탐색
    m_px = re.search(r"현재가[^0-9]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*원", html)
    nav_list = re.findall(r"기준가\(NAV\)[^0-9]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*원", html)
    if m_px and nav_list:
        price = float(m_px.group(1).replace(",", ""))
        nav = float(nav_list[-1].replace(",", ""))
        return price, nav

    raise ValueError("ACE page: price/nav not found (meta+html)")


def send_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    r.raise_for_status()

def fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"

if __name__ == "__main__":
    FORCE_RUN = os.environ.get("FORCE_RUN", "0") == "1"        # 장중 체크 무시(테스트)
    TEST_MESSAGE = os.environ.get("TEST_MESSAGE", "0") == "1"  # 조건 무시(테스트)

    if (not FORCE_RUN) and (not is_korean_market_hours()):
        raise SystemExit(0)

    usdkrw = get_usdkrw()
    dom_g = get_domestic_gold_krw_per_g()
    intl_usd_oz = get_international_gold_usd_per_oz()
    intl_krw_g = intl_usd_oz * usdkrw / OZ_TO_G
    kimchi = (dom_g - intl_krw_g) / intl_krw_g * 100.0

    ace_px, ace_nav = get_ace_411060_price_and_nav()
    ace_prem = (ace_px - ace_nav) / ace_nav * 100.0

    TH = float(os.environ.get("KIMCHI_THRESHOLD", "1.5"))
    if (not TEST_MESSAGE) and (abs(kimchi) < TH and abs(ace_prem) < TH):
        raise SystemExit(0)

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    msg = "\n".join([
        f"[ALERT] {now}",
        f"- 금 김프(국내 vs 국제환산): {fmt_pct(kimchi)}",
        f"- 411060 프리미엄(NAV): {fmt_pct(ace_prem)}",
        f"- 조건: |값| ≥ {TH:.2f}%",
        f"- 테스트: FORCE_RUN={int(FORCE_RUN)}, TEST_MESSAGE={int(TEST_MESSAGE)}",
    ])
    send_telegram(msg)
