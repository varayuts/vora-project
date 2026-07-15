# app/providers/search/fetch_and_extract.py
from __future__ import annotations
import asyncio, httpx, tldextract
from bs4 import BeautifulSoup
from readability import Document
import trafilatura

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.5",
}
BLOCKED_DOMAINS = {"facebook.com", "x.com", "twitter.com", "tiktok.com", "youtube.com"}
MIN_TEXT_LEN = 600  # ต้องยาวพอจะสรุปได้

def domain_of(url: str) -> str:
    ext = tldextract.extract(url)
    return ".".join([p for p in [ext.domain, ext.suffix] if p])

def extract_text(html: bytes, url: str) -> str:
    # พยายามใช้ trafilatura ก่อน
    txt = trafilatura.extract(html, url=url, include_comments=False,
                              include_tables=False, no_fallback=True,
                              favor_recall=True)
    if txt:
        return txt.strip()
    # fallback: readability + bs4
    doc = Document(html)
    summary_html = doc.summary()
    soup = BeautifulSoup(summary_html, "lxml")
    return soup.get_text(separator="\n", strip=True)

async def fetch_one(client: httpx.AsyncClient, url: str) -> dict:
    try:
        dom = domain_of(url)
        if dom in BLOCKED_DOMAINS:
            return {"url": url, "ok": False, "reason": "blocked_domain"}
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        ctype = r.headers.get("content-type","")
        if "text/html" not in ctype:
            return {"url": url, "ok": False, "reason": f"ctype={ctype}"}
        text = extract_text(r.content, url)
        if not text or len(text) < MIN_TEXT_LEN:
            return {"url": url, "ok": False, "reason": "too_short"}
        return {"url": url, "ok": True, "domain": dom, "text": text}
    except Exception as e:
        return {"url": url, "ok": False, "reason": repr(e)}

async def fetch_many(urls: list[str], concurrency: int = 5) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        async def run(u):
            async with sem:
                return await fetch_one(client, u)
        return await asyncio.gather(*[run(u) for u in urls])


