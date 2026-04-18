"""
X Search Deck — Railway/Render com Playwright aba única
"""
from __future__ import annotations
import asyncio, json, logging, os, re, urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

PORT             = int(os.environ.get("PORT", 8765))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 90))
STAGGER_SECONDS  = int(os.environ.get("STAGGER_SECONDS", 8))
MAX_TWEETS       = int(os.environ.get("MAX_TWEETS", 20))
PAGE_WAIT        = float(os.environ.get("PAGE_WAIT", 7))
X_COOKIES_JSON   = os.environ.get("X_COOKIES_JSON", "")

LAUNCH_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-gpu", "--disable-software-rasterizer", "--disable-extensions",
    "--disable-background-networking", "--disable-default-apps",
    "--disable-sync", "--mute-audio", "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--js-flags=--max-old-space-size=192",
]


def normalize_cookies(raw):
    SM = {"no_restriction":"None","lax":"Lax","strict":"Strict",None:"None"}
    out = []
    for c in raw:
        exp = c.get("expires") or c.get("expirationDate")
        out.append({
            "name": c["name"], "value": c["value"],
            "domain": c.get("domain", ".x.com"), "path": c.get("path", "/"),
            "expires": float(exp) if exp else -1,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure":   bool(c.get("secure", True)),
            "sameSite": SM.get(c.get("sameSite"), "None"),
        })
    return out


def build_url(query, sort="live"):
    q = re.sub(r"\s+", " ", query.replace("\n", " ")).strip()
    return f"https://x.com/search?q={urllib.parse.quote(q)}&f={sort}&src=typed_query"


# ── Extração ──────────────────────────────────────────────

async def extract_tweets(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=14000)
    except Exception:
        return []
    articles = await page.query_selector_all('article[data-testid="tweet"]')
    tweets = []
    for art in articles[:MAX_TWEETS]:
        try:
            t = await _one(art)
            if t.get("text") or t.get("author_name"):
                tweets.append(t)
        except Exception:
            pass
    return tweets


async def _one(art) -> dict:
    t = {}
    el = await art.query_selector('[data-testid="tweetText"]')
    t["text"] = (await el.inner_text()).strip() if el else ""

    try:
        r = await art.evaluate("""el => {
            const un = el.querySelector('[data-testid="User-Name"]');
            if (!un) return ['',''];
            let name='', handle='';
            for (const a of Array.from(un.querySelectorAll('a')))
                for (const s of Array.from(a.querySelectorAll('span'))) {
                    const t = s.innerText.trim();
                    if (!t) continue;
                    if (t.startsWith('@') && !handle) handle = t;
                    else if (!t.startsWith('@') && !name) name = t;
                }
            return [name, handle];
        }""")
        t["author_name"], t["author_handle"] = r[0], r[1]
    except Exception:
        t["author_name"] = t["author_handle"] = ""

    av = await art.query_selector('img[src*="profile_images"]')
    src = await av.get_attribute("src") if av else ""
    t["avatar"] = src.replace("_normal", "_bigger") if src else ""

    for key, tid in [("replies","reply"),("retweets","retweet"),("likes","like")]:
        try:
            v = await art.evaluate(f"""el => {{
                const b = el.querySelector('[data-testid="{tid}"]');
                if (!b) return '0';
                for (const s of Array.from(b.querySelectorAll('span'))) {{
                    const t = s.innerText.trim();
                    if (t && /^[\\d,\\.KkMm]+$/.test(t)) return t;
                }}
                return '0';
            }}""")
            t[key] = v or "0"
        except Exception:
            t[key] = "0"

    try:
        t["views"] = await art.evaluate("""el => {
            const a = el.querySelector('a[href*="/analytics"]');
            if (!a) return '';
            for (const s of Array.from(a.querySelectorAll('span'))) {
                const t = s.innerText.trim();
                if (t && /^[\\d,.KkMm]+$/.test(t)) return t;
            }
            return '';
        }""") or ""
    except Exception:
        t["views"] = ""

    try:
        r = await art.evaluate("""el => {
            const time = el.querySelector('time');
            if (!time) return ['',''];
            const ts = time.getAttribute('datetime') || '';
            let a = time.parentElement;
            while (a && a.tagName !== 'A') a = a.parentElement;
            return [a ? 'https://x.com' + a.getAttribute('href') : '', ts];
        }""")
        t["url"], t["timestamp"] = r[0], r[1]
    except Exception:
        t["url"] = t["timestamp"] = ""

    return t


# ── Browser — aba única ───────────────────────────────────

class BrowserManager:
    def __init__(self):
        self._pw   = None
        self.browser: Optional[Browser]        = None
        self.context: Optional[BrowserContext] = None
        self.page:    Optional[Page]           = None
        self._lock = asyncio.Lock()

    async def _launch(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        try:
            if self.browser and self.browser.is_connected():
                await self.browser.close()
        except Exception:
            pass
        self.page = None

        self.browser = await self._pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        self.context = await self.browser.new_context(
            viewport={"width": 1024, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        if X_COOKIES_JSON:
            try:
                await self.context.add_cookies(normalize_cookies(json.loads(X_COOKIES_JSON)))
                log.info("✅ Cookies injetados")
            except Exception as e:
                log.error(f"❌ Cookies: {e}")

        self.page = await self.context.new_page()
        await self.page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot,mp4,mp3}",
            lambda r: r.abort()
        )
        await self.page.route("**/ads/**",       lambda r: r.abort())
        await self.page.route("**/analytics/**", lambda r: r.abort())

    async def start(self):
        await self._launch()
        try:
            await self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(3)
            if "login" in self.page.url or "i/flow" in self.page.url:
                log.error("❌ Não autenticado")
            else:
                log.info("✅ Autenticado no X")
        except Exception as e:
            log.warning(f"Auth check: {e}")

    async def _ok(self):
        try:
            return (self.browser and self.browser.is_connected()
                    and self.page and not self.page.is_closed())
        except Exception:
            return False

    async def fetch(self, url: str) -> list[dict]:
        async with self._lock:
            if not await self._ok():
                log.warning("⚠️  Browser caiu — relançando...")
                await self._launch()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(PAGE_WAIT)
            return await extract_tweets(self.page)

    async def stop(self):
        try:
            if self.browser: await self.browser.close()
        except Exception: pass
        try:
            if self._pw: await self._pw.stop()
        except Exception: pass


# ── App ───────────────────────────────────────────────────

class XDeckApp:
    def __init__(self):
        self.bm = BrowserManager()
        self.subscriptions: dict[int, dict] = {}
        self.results:       dict[int, list] = {}
        self.clients:       set[web.WebSocketResponse] = set()

    async def startup(self, app):
        await self.bm.start()
        asyncio.create_task(self._refresh_loop())
        log.info(f"🚀 X Search Deck online — porta {PORT}")

    async def shutdown(self, app):
        await self.bm.stop()

    async def index_handler(self, request):
        return web.Response(
            text=Path("interface.html").read_text(encoding="utf-8"),
            content_type="text/html"
        )

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.clients.add(ws)
        log.info(f"Cliente conectado ({len(self.clients)})")

        for col_id, tweets in self.results.items():
            try:
                await ws.send_str(json.dumps({
                    "type":"results","column":col_id,
                    "tweets":tweets,"updated":"—","count":len(tweets)
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
                    self.subscriptions = {
                        i: col for i, col in enumerate(data.get("columns", []))
                        if col.get("query", "").strip()
                    }
                    log.info(f"Subscription: {len(self.subscriptions)} colunas")
                    asyncio.create_task(self.refresh_all())
                elif data.get("type") == "refresh_one":
                    col_id = data.get("column")
                    if col_id is not None:
                        asyncio.create_task(self.refresh_column(col_id))

        self.clients.discard(ws)
        log.info(f"Cliente desconectado ({len(self.clients)})")
        return ws

    async def refresh_column(self, col_id: int):
        cfg = self.subscriptions.get(col_id)
        if not cfg or not cfg.get("query", "").strip():
            return
        await self.broadcast({"type":"status","column":col_id,"status":"loading"})
        try:
            url = build_url(cfg["query"], cfg.get("sort","live"))
            log.info(f"Col {col_id+1}: coletando...")
            tweets = await self.bm.fetch(url)
            self.results[col_id] = tweets
            ts = datetime.now().strftime("%H:%M:%S")
            await self.broadcast({"type":"results","column":col_id,
                "tweets":tweets,"updated":ts,"count":len(tweets)})
            await self.broadcast({"type":"status","column":col_id,"status":"ok"})
            log.info(f"Col {col_id+1}: ✅ {len(tweets)} tweets")
        except Exception as e:
            log.error(f"Col {col_id+1}: ❌ {e}")
            await self.broadcast({"type":"status","column":col_id,
                "status":"error","message":str(e)[:120]})

    async def refresh_all(self):
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
    app  = web.Application()
    app.router.add_get("/",   deck.index_handler)
    app.router.add_get("/ws", deck.ws_handler)
    app.on_startup.append(deck.startup)
    app.on_shutdown.append(deck.shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=PORT)
