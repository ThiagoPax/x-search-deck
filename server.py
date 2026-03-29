"""
X Search Deck — servidor para Render.com
HTTP + WebSocket via aiohttp | Playwright headless com cookies do X
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PORT             = int(os.environ.get("PORT", 8765))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 60))
STAGGER_SECONDS  = int(os.environ.get("STAGGER_SECONDS", 8))
MAX_TWEETS       = int(os.environ.get("MAX_TWEETS", 25))
PAGE_WAIT        = float(os.environ.get("PAGE_WAIT", 7))
X_COOKIES_JSON   = os.environ.get("X_COOKIES_JSON", "")

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--mute-audio",
    "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--js-flags=--max-old-space-size=256",
]


def normalize_cookies(raw: list[dict]) -> list[dict]:
    SAMESITE_MAP = {"no_restriction": "None", "lax": "Lax", "strict": "Strict", None: "None"}
    result = []
    for c in raw:
        expires = c.get("expires") or c.get("expirationDate")
        same = c.get("sameSite")
        result.append({
            "name":     c["name"],
            "value":    c["value"],
            "domain":   c.get("domain", ".x.com"),
            "path":     c.get("path", "/"),
            "expires":  float(expires) if expires else -1,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure":   bool(c.get("secure", True)),
            "sameSite": SAMESITE_MAP.get(same, "None") if isinstance(same, (str, type(None))) else "None",
        })
    return result


def build_url(query: str, sort: str = "live") -> str:
    compact = re.sub(r"\s+", " ", query.replace("\n", " ")).strip()
    return f"https://x.com/search?q={urllib.parse.quote(compact)}&f={sort}&src=typed_query"


# ── Extração ──────────────────────────────────────────────

async def extract_tweets(page: Page) -> list[dict]:
    tweets = []
    try:
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
    except Exception:
        log.warning("Timeout aguardando tweets")
        return tweets
    articles = await page.query_selector_all('article[data-testid="tweet"]')
    log.info(f"  {len(articles)} artigos, extraindo até {MAX_TWEETS}")
    for article in articles[:MAX_TWEETS]:
        try:
            t = await _extract_one(article)
            if t.get("text") or t.get("author_name"):
                tweets.append(t)
        except Exception as e:
            log.debug(f"Erro em tweet: {e}")
    return tweets


async def _extract_one(article) -> dict:
    t = {}
    text_el = await article.query_selector('[data-testid="tweetText"]')
    t["text"] = (await text_el.inner_text()).strip() if text_el else ""
    t["author_name"], t["author_handle"] = await _get_author(article)
    av = await article.query_selector('img[src*="profile_images"]')
    raw_av = await av.get_attribute("src") if av else ""
    t["avatar"] = raw_av.replace("_normal", "_bigger") if raw_av else ""
    for key, testid in [("replies","reply"),("retweets","retweet"),("likes","like")]:
        t[key] = await _get_metric(article, testid)
    t["views"] = await _get_views(article)
    t["url"], t["timestamp"] = await _get_link(article)
    return t


async def _get_author(article):
    try:
        r = await article.evaluate("""el => {
            const un = el.querySelector('[data-testid="User-Name"]');
            if (!un) return ['',''];
            const links = Array.from(un.querySelectorAll('a'));
            let name='', handle='';
            for (const a of links) {
                for (const s of Array.from(a.querySelectorAll('span'))) {
                    const t = s.innerText.trim();
                    if (!t) continue;
                    if (t.startsWith('@') && !handle) handle = t;
                    else if (!t.startsWith('@') && !name) name = t;
                }
            }
            return [name, handle];
        }""")
        return r[0], r[1]
    except Exception:
        return "", ""


async def _get_metric(article, testid):
    try:
        v = await article.evaluate(f"""el => {{
            const btn = el.querySelector('[data-testid="{testid}"]');
            if (!btn) return '0';
            for (const s of Array.from(btn.querySelectorAll('span'))) {{
                const t = s.innerText.trim();
                if (t && /^[\\d,\\.KkMm]+$/.test(t)) return t;
            }}
            return '0';
        }}""")
        return v or "0"
    except Exception:
        return "0"


async def _get_views(article):
    try:
        v = await article.evaluate("""el => {
            const a = el.querySelector('a[href*="/analytics"]');
            if (a) {
                for (const s of Array.from(a.querySelectorAll('span'))) {
                    const t = s.innerText.trim();
                    if (t && /^[\\d,.KkMm]+$/.test(t)) return t;
                }
            }
            return '';
        }""")
        return v or ""
    except Exception:
        return ""


async def _get_link(article):
    try:
        r = await article.evaluate("""el => {
            const time = el.querySelector('time');
            if (!time) return ['',''];
            const ts = time.getAttribute('datetime') || '';
            let a = time.parentElement;
            while (a && a.tagName !== 'A') a = a.parentElement;
            const href = a ? a.getAttribute('href') : '';
            return [href ? 'https://x.com'+href : '', ts];
        }""")
        return r[0], r[1]
    except Exception:
        return "", ""


# ── Browser ───────────────────────────────────────────────

class BrowserManager:
    def __init__(self):
        self._pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.pages: dict[int, Page] = {}
        self._lock = asyncio.Lock()

    async def _launch(self):
        """Lança browser + context + injeta cookies. Seguro para re-chamar."""
        if self._pw is None:
            self._pw = await async_playwright().start()
        try:
            if self.browser and self.browser.is_connected():
                await self.browser.close()
        except Exception:
            pass
        self.pages = {}

        self.browser = await self._pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        if X_COOKIES_JSON:
            try:
                cookies = normalize_cookies(json.loads(X_COOKIES_JSON))
                await self.context.add_cookies(cookies)
                log.info(f"✅ {len(cookies)} cookies injetados")
            except Exception as e:
                log.error(f"❌ Cookies: {e}")

    async def start(self):
        await self._launch()
        page = await self.context.new_page()
        try:
            await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            if "login" in page.url or "i/flow" in page.url:
                log.error("❌ Não autenticado")
            else:
                log.info("✅ Autenticado no X")
        except Exception as e:
            log.warning(f"Auth check: {e}")
        finally:
            await page.close()

    async def _browser_ok(self) -> bool:
        try:
            return self.browser is not None and self.browser.is_connected()
        except Exception:
            return False

    async def get_page(self, col_id: int) -> Page:
        if not await self._browser_ok():
            log.warning("⚠️  Browser caiu — reiniciando...")
            async with self._lock:
                if not await self._browser_ok():
                    await self._launch()

        if col_id in self.pages:
            try:
                await self.pages[col_id].title()
                return self.pages[col_id]
            except Exception:
                del self.pages[col_id]

        page = await self.context.new_page()
        # Bloqueia imagens e fontes para economizar memória
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot}",
            lambda r: r.abort()
        )
        self.pages[col_id] = page
        return page

    async def stop(self):
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass


# ── App principal ─────────────────────────────────────────

class XDeckApp:
    def __init__(self):
        self.bm = BrowserManager()
        self.subscriptions: dict[int, dict] = {}
        self.results: dict[int, list] = {}
        self.clients: set[web.WebSocketResponse] = set()

    async def startup(self, app):
        await self.bm.start()
        asyncio.create_task(self._refresh_loop())
        log.info(f"🚀 X Search Deck online — porta {PORT}")

    async def shutdown(self, app):
        await self.bm.stop()

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.clients.add(ws)
        log.info(f"Cliente conectado ({len(self.clients)})")

        for col_id, tweets in self.results.items():
            try:
                await ws.send_str(json.dumps({
                    "type": "results", "column": col_id,
                    "tweets": tweets, "updated": "—", "count": len(tweets),
                }, ensure_ascii=False))
            except Exception:
                pass

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if data.get("type") == "subscribe":
                    self.subscriptions = {}
                    for i, col in enumerate(data.get("columns", [])):
                        if col.get("query", "").strip():
                            self.subscriptions[i] = col
                    log.info(f"Subscription: {len(self.subscriptions)} colunas")
                    asyncio.create_task(self.refresh_all())
                elif data.get("type") == "refresh_one":
                    col_id = data.get("column")
                    if col_id is not None:
                        asyncio.create_task(self.refresh_column(col_id))

        self.clients.discard(ws)
        log.info(f"Cliente desconectado ({len(self.clients)})")
        return ws

    async def index_handler(self, request):
        html = Path("interface.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def refresh_column(self, col_id: int):
        cfg = self.subscriptions.get(col_id)
        if not cfg:
            return
        query = cfg.get("query", "").strip()
        if not query:
            return

        await self.broadcast({"type": "status", "column": col_id, "status": "loading"})
        try:
            url = build_url(query, cfg.get("sort", "live"))
            page = await self.bm.get_page(col_id)
            log.info(f"Col {col_id+1}: coletando...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(PAGE_WAIT)
            tweets = await extract_tweets(page)
            self.results[col_id] = tweets
            ts = datetime.now().strftime("%H:%M:%S")
            await self.broadcast({
                "type": "results", "column": col_id,
                "tweets": tweets, "updated": ts, "count": len(tweets),
            })
            await self.broadcast({"type": "status", "column": col_id, "status": "ok"})
            log.info(f"Col {col_id+1}: ✅ {len(tweets)} tweets")
        except Exception as e:
            log.error(f"Col {col_id+1}: ❌ {e}")
            # Descarta a página com problema para o próximo uso pegar uma nova
            self.bm.pages.pop(col_id, None)
            await self.broadcast({
                "type": "status", "column": col_id,
                "status": "error", "message": str(e)[:120],
            })

    async def refresh_all(self):
        # Processa colunas uma por vez para não sobrecarregar memória
        for col_id in sorted(self.subscriptions):
            await self.refresh_column(col_id)
            await asyncio.sleep(STAGGER_SECONDS)

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            if self.subscriptions:
                log.info("⏰ Auto-refresh")
                await self.refresh_all()

    async def broadcast(self, message: dict):
        data = json.dumps(message, ensure_ascii=False)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        self.clients -= dead


def create_app():
    deck = XDeckApp()
    app = web.Application()
    app.router.add_get("/",   deck.index_handler)
    app.router.add_get("/ws", deck.ws_handler)
    app.on_startup.append(deck.startup)
    app.on_shutdown.append(deck.shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=PORT)
