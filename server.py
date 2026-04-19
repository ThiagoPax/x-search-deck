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
from email_alerts import get_scheduler
from openai_service import (
    OpenAIConfigError,
    OpenAIEmptyResponseError,
    OpenAIModelError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
    OpenAIUpstreamError,
    summarize_column,
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

PORT             = int(os.environ.get("PORT", 8765))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 90))
STAGGER_SECONDS  = int(os.environ.get("STAGGER_SECONDS", 8))
MAX_TWEETS       = int(os.environ.get("MAX_TWEETS", 100))
MAX_SCROLLS      = int(os.environ.get("MAX_SCROLLS", 12))
SCROLL_WAIT      = float(os.environ.get("SCROLL_WAIT", 1.1))
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


def apply_column_filters(cfg: dict) -> str:
    q = re.sub(r"\s+", " ", (cfg.get("query") or "").replace("\n", " ")).strip()
    date_from = (cfg.get("date_from") or "").strip()
    date_to = (cfg.get("date_to") or "").strip()
    language = (cfg.get("language") or "").strip().lower()
    muted = (cfg.get("muted") or "").strip()
    min_faves = _clean_int(cfg.get("min_faves"))
    min_replies = _clean_int(cfg.get("min_replies"))
    min_retweets = _clean_int(cfg.get("min_retweets"))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from) and "since:" not in q:
        q = f"{q} since:{date_from}".strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to) and "until:" not in q:
        q = f"{q} until:{date_to}".strip()
    if cfg.get("exclude_retweets") and "-filter:retweets" not in q:
        q = f"{q} -filter:retweets".strip()
    if min_faves is not None and "min_faves:" not in q:
        q = f"{q} min_faves:{min_faves}".strip()
    if min_replies is not None and "min_replies:" not in q:
        q = f"{q} min_replies:{min_replies}".strip()
    if min_retweets is not None and "min_retweets:" not in q:
        q = f"{q} min_retweets:{min_retweets}".strip()
    if cfg.get("filter_media") and "filter:media" not in q:
        q = f"{q} filter:media".strip()
    if cfg.get("filter_verified") and "filter:verified" not in q:
        q = f"{q} filter:verified".strip()
    if re.fullmatch(r"[a-z]{2,3}", language) and "lang:" not in q:
        q = f"{q} lang:{language}".strip()
    for negative in _negative_query_terms(muted):
        if negative not in q:
            q = f"{q} {negative}".strip()
    return q


def _negative_query_terms(raw: str) -> list[str]:
    if not raw:
        return []
    pieces = re.findall(r'"[^"]+"|[^,\n;]+', raw)
    terms: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        term = piece.strip()
        if not term:
            continue
        quoted = len(term) >= 2 and term[0] == '"' and term[-1] == '"'
        inner = term[1:-1].strip() if quoted else term
        if not inner:
            continue
        if term.startswith("-"):
            negative = term
        elif inner.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_]{1,15}", inner):
            negative = f"-from:{inner[1:]}"
        elif re.fullmatch(r"from:[A-Za-z0-9_]{1,15}", inner, flags=re.I):
            negative = f"-{inner}"
        elif quoted or re.search(r"\s", inner):
            negative = f'-"{inner}"'
        else:
            negative = f"-{inner}"
        if negative not in seen:
            seen.add(negative)
            terms.append(negative)
    return terms


def _clean_int(value) -> Optional[int]:
    try:
        if value in ("", None):
            return None
        n = int(value)
        if n < 0:
            return None
        return min(n, 10_000_000)
    except Exception:
        return None


# ── Extração ──────────────────────────────────────────────

async def extract_tweets(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=14000)
    except Exception:
        return []
    tweets = []
    seen = set()
    stagnant = 0
    last_count = 0
    for _ in range(MAX_SCROLLS + 1):
        articles = await page.query_selector_all('article[data-testid="tweet"]')
        for art in articles:
            if len(tweets) >= MAX_TWEETS:
                break
            try:
                t = await _one(art)
                if not (t.get("text") or t.get("author_name")):
                    continue
                key = t.get("url") or f"{t.get('author_handle','')}:{t.get('text','')}"[:320]
                if key in seen:
                    continue
                seen.add(key)
                tweets.append(t)
            except Exception:
                pass
        if len(tweets) >= MAX_TWEETS:
            break
        if len(tweets) == last_count:
            stagnant += 1
        else:
            stagnant = 0
        if stagnant >= 3:
            break
        last_count = len(tweets)
        await page.mouse.wheel(0, 2600)
        await asyncio.sleep(SCROLL_WAIT)
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

    try:
        t["verified"] = bool(await art.evaluate("""el => {
            const un = el.querySelector('[data-testid="User-Name"]');
            if (!un) return false;
            const label = (un.innerText + ' ' + un.getAttribute('aria-label') + ' ' + un.outerHTML).toLowerCase();
            return label.includes('verified') || label.includes('verificado') || label.includes('is a verified');
        }"""))
    except Exception:
        t["verified"] = False

    av = await art.query_selector('img[src*="profile_images"]')
    src = await av.get_attribute("src") if av else ""
    t["avatar"] = src.replace("_normal", "_bigger") if src else ""

    try:
        t["media"] = await art.evaluate("""el => {
            const out = [];
            const seen = new Set();
            const add = (url, type) => {
                if (!url || seen.has(url)) return;
                seen.add(url);
                out.push({url, type});
            };
            for (const img of Array.from(el.querySelectorAll('img'))) {
                const src = img.currentSrc || img.src || '';
                if (!src || src.includes('profile_images') || src.includes('emoji') || src.includes('hashflags')) continue;
                if (src.includes('pbs.twimg.com/media/')) add(src, 'photo');
                else if (src.includes('pbs.twimg.com/tweet_video_thumb/')) add(src, 'gif');
                else if (src.includes('video_thumb')) add(src, 'video');
            }
            if (el.querySelector('video')) {
                const img = el.querySelector('img[src*="pbs.twimg.com"]');
                const src = img ? (img.currentSrc || img.src || '') : '';
                add(src, 'video');
            }
            return out.slice(0, 4);
        }""") or []
    except Exception:
        t["media"] = []

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
        self.subscriptions: dict[int | str, dict] = {}
        self.results:       dict[int | str, list] = {}
        self.clients:       set[web.WebSocketResponse] = set()
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_again = False
        self._generation = 0

    @staticmethod
    def _col_key(idx: int, col: dict):
        raw = col.get("id")
        if raw not in ("", None):
            return str(raw)
        return idx

    @staticmethod
    def _col_label(col_id) -> str:
        if isinstance(col_id, int):
            return f"Col {col_id + 1}"
        return f"Col {col_id}"

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

    async def alert_config_handler(self, request):
        scheduler = get_scheduler()
        if request.method == "GET":
            return web.json_response(scheduler.get_config())
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "JSON invalido"}, status=400)
        cfg = scheduler.save_config(data)
        return web.json_response(cfg)

    async def alert_preview_handler(self, request):
        scheduler = get_scheduler()
        try:
            data = await request.json()
        except Exception:
            data = {}
        title = data.get("title") or f"Preview X Search Deck - {datetime.now().strftime('%H:%M')}"
        subject = data.get("subject") or f"[ALERTA X] Preview manual - {datetime.now().strftime('%H:%M')}"
        sent = scheduler.send_digest(title=title, subject=subject)
        status = 200 if sent else 400
        return web.json_response({
            "sent": sent,
            "message": "Preview enviado" if sent else "Sem tweets acima do threshold ou SMTP/destinatarios ausentes"
        }, status=status)

    async def alert_test_email_handler(self, request):
        scheduler = get_scheduler()
        try:
            data = await request.json()
        except Exception:
            data = {}
        result = scheduler.send_test_email(data if isinstance(data, dict) else {})
        return web.json_response(result, status=200 if result.get("ok") else 400)

    async def column_summary_handler(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "JSON invalido"}, status=400)
        tweets = data.get("tweets") or []
        if not isinstance(tweets, list):
            return web.json_response({"error": "tweets precisa ser uma lista"}, status=400)
        try:
            text = await summarize_column(tweets, data.get("column_name") or "")
            return web.json_response({"summary": text})
        except OpenAIConfigError as e:
            return web.json_response({"error": str(e)}, status=400)
        except OpenAIModelError as e:
            return web.json_response({"error": str(e)}, status=400)
        except OpenAIRateLimitError as e:
            return web.json_response({"error": str(e)}, status=429)
        except OpenAITimeoutError as e:
            return web.json_response({"error": str(e)}, status=504)
        except OpenAIEmptyResponseError as e:
            return web.json_response({"error": str(e)}, status=502)
        except OpenAIUpstreamError as e:
            return web.json_response({"error": str(e)}, status=502)
        except Exception as e:
            log.exception("Resumo IA falhou de forma inesperada")
            return web.json_response({"error": "Erro inesperado ao gerar resumo IA. Verifique os logs do backend."}, status=502)

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
                        self._col_key(i, col): col for i, col in enumerate(data.get("columns", []))
                        if col.get("query", "").strip()
                    }
                    self._generation += 1
                    log.info(f"Subscription: {len(self.subscriptions)} colunas")
                    if data.get("refresh", True):
                        self.schedule_refresh_all()
                elif data.get("type") == "refresh_one":
                    col_id = data.get("column")
                    if col_id is not None:
                        cfg = self.subscriptions.get(col_id)
                        if cfg:
                            asyncio.create_task(self.refresh_column(col_id, cfg.copy(), self._generation))

        self.clients.discard(ws)
        log.info(f"Cliente desconectado ({len(self.clients)})")
        return ws

    async def refresh_column(self, col_id, cfg: Optional[dict] = None, generation: Optional[int] = None):
        cfg = cfg or self.subscriptions.get(col_id)
        if not cfg or not cfg.get("query", "").strip():
            return
        await self.broadcast({"type":"status","column":col_id,"status":"loading"})
        try:
            filtered_query = apply_column_filters(cfg)
            url = build_url(filtered_query, cfg.get("sort","live"))
            label = self._col_label(col_id)
            log.info(f"{label}: coletando...")
            tweets = await self.bm.fetch(url)
            current_cfg = self.subscriptions.get(col_id)
            if generation is not None and generation != self._generation:
                log.info(f"{label}: resultado antigo descartado")
                return
            if current_cfg is not None and _cfg_signature(current_cfg) != _cfg_signature(cfg):
                log.info(f"{label}: configuração mudou durante coleta; descartando")
                return
            if not tweets and self.results.get(col_id):
                ts = datetime.now().strftime("%H:%M:%S")
                await self.broadcast({"type":"results","column":col_id,
                    "tweets":self.results[col_id],"updated":f"{ts} · mantido","count":len(self.results[col_id])})
                await self.broadcast({"type":"status","column":col_id,
                    "status":"error","message":"X não renderizou resultados nesta coleta; mantendo últimos tweets."})
                log.warning(f"{label}: 0 tweets transitório; mantendo {len(self.results[col_id])}")
                return
            self.results[col_id] = tweets
            ts = datetime.now().strftime("%H:%M:%S")
            await self.broadcast({"type":"results","column":col_id,
                "tweets":tweets,"updated":ts,"count":len(tweets)})
            await self.broadcast({"type":"status","column":col_id,"status":"ok"})
            log.info(f"{label}: ✅ {len(tweets)} tweets")
            col_label = cfg.get("name") or label
            get_scheduler().ingest(col_id, col_label, tweets)
        except Exception as e:
            log.error(f"{self._col_label(col_id)}: ❌ {e}")
            await self.broadcast({"type":"status","column":col_id,
                "status":"error","message":str(e)[:120]})

    def schedule_refresh_all(self):
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_again = True
            log.info("Refresh global já em andamento; novo ciclo enfileirado")
            return
        self._refresh_task = asyncio.create_task(self.refresh_all())

    async def refresh_all(self):
        while True:
            self._refresh_again = False
            generation = self._generation
            snapshot = {col_id: cfg.copy() for col_id, cfg in self.subscriptions.items()}
            for col_id in sorted(snapshot, key=str):
                await self.refresh_column(col_id, snapshot[col_id], generation)
                if generation != self._generation:
                    log.info("Subscriptions mudaram; interrompendo ciclo antigo")
                    break
                await asyncio.sleep(STAGGER_SECONDS)
            get_scheduler().dispatch_scheduled()
            if not self._refresh_again:
                break

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            if self.subscriptions:
                log.info("⏰ Auto-refresh")
                self.schedule_refresh_all()

    async def broadcast(self, message: dict):
        data = json.dumps(message, ensure_ascii=False)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        self.clients -= dead


def _cfg_signature(cfg: dict) -> str:
    return json.dumps(cfg or {}, sort_keys=True, ensure_ascii=False)


def create_app():
    deck = XDeckApp()
    app  = web.Application()
    app.router.add_get("/",   deck.index_handler)
    app.router.add_get("/ws", deck.ws_handler)
    app.router.add_get("/api/alerts/config", deck.alert_config_handler)
    app.router.add_post("/api/alerts/config", deck.alert_config_handler)
    app.router.add_post("/api/alerts/preview", deck.alert_preview_handler)
    app.router.add_post("/api/alerts/test-email", deck.alert_test_email_handler)
    app.router.add_post("/api/ai/column-summary", deck.column_summary_handler)
    app.on_startup.append(deck.startup)
    app.on_shutdown.append(deck.shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=PORT)
