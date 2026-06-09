"""
X Search Deck — Railway/Render com Playwright efêmero
"""
from __future__ import annotations
import asyncio, json, logging, os, re, urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from email_alerts import get_scheduler
from operational_mode import get_operational_mode, is_critical_window_now
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
REFRESH_GLOBAL_TIMEOUT = int(os.environ.get("REFRESH_GLOBAL_TIMEOUT", 120))
STAGGER_SECONDS  = int(os.environ.get("STAGGER_SECONDS", 8))
MAX_TWEETS       = int(os.environ.get("MAX_TWEETS", 100))
MAX_SCROLLS      = int(os.environ.get("MAX_SCROLLS", 12))
SCROLL_WAIT      = float(os.environ.get("SCROLL_WAIT", 1.1))
PAGE_WAIT        = float(os.environ.get("PAGE_WAIT", 7))
X_COOKIES_JSON   = os.environ.get("X_COOKIES_JSON", "")


def current_rss_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) / 1024
    except Exception:
        pass
    return 0.0


def format_rss() -> str:
    rss = current_rss_mb()
    return f"{rss:.1f} MB" if rss else "indisponivel"

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


# ── Browser — efêmero por busca ────────────────────────────

class BrowserManager:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def _new_page(self, pw: Playwright) -> tuple[Browser, BrowserContext, Page]:
        log.info(f"🧠 RSS antes de abrir Chromium: {format_rss()}")
        browser = await pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        log.info(f"🟢 Chromium aberto — RSS: {format_rss()}")
        context = await browser.new_context(
            viewport={"width": 1024, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        log.info(f"🟢 Contexto Playwright aberto — RSS: {format_rss()}")
        if X_COOKIES_JSON:
            try:
                await context.add_cookies(normalize_cookies(json.loads(X_COOKIES_JSON)))
                log.info("✅ Cookies injetados no contexto efêmero")
            except Exception as e:
                log.error(f"❌ Cookies: {e}")

        page = await context.new_page()
        log.info(f"🟢 Página Playwright aberta — RSS: {format_rss()}")
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot,mp4,mp3}",
            lambda r: r.abort()
        )
        await page.route("**/ads/**",       lambda r: r.abort())
        await page.route("**/analytics/**", lambda r: r.abort())
        return browser, context, page

    async def _close(self, page: Optional[Page], context: Optional[BrowserContext], browser: Optional[Browser]):
        if page:
            try:
                await page.close()
                log.info(f"🔴 Página Playwright fechada — RSS: {format_rss()}")
            except Exception as e:
                log.warning(f"Falha ao fechar página Playwright: {e}")
        if context:
            try:
                await context.close()
                log.info(f"🔴 Contexto Playwright fechado — RSS: {format_rss()}")
            except Exception as e:
                log.warning(f"Falha ao fechar contexto Playwright: {e}")
        if browser:
            try:
                await browser.close()
                log.info(f"🔴 Chromium fechado — RSS: {format_rss()}")
            except Exception as e:
                log.warning(f"Falha ao fechar Chromium: {e}")

    async def start(self):
        log.info("Playwright em modo efêmero: Chromium só abre durante buscas")

    async def fetch(self, url: str) -> list[dict]:
        async with self._lock:
            browser: Optional[Browser] = None
            context: Optional[BrowserContext] = None
            page: Optional[Page] = None
            pw = None
            try:
                log.info(f"🧠 RSS antes da busca Playwright: {format_rss()}")
                pw = await async_playwright().start()
                browser, context, page = await self._new_page(pw)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if "login" in page.url or "i/flow" in page.url:
                    log.error("❌ Não autenticado no X")
                await asyncio.sleep(PAGE_WAIT)
                return await extract_tweets(page)
            finally:
                await self._close(page, context, browser)
                if pw:
                    try:
                        await pw.stop()
                        log.info(f"🔴 Playwright parado — RSS: {format_rss()}")
                    except Exception as e:
                        log.warning(f"Falha ao parar Playwright: {e}")
                log.info(f"🧠 RSS depois da busca Playwright: {format_rss()}")

    async def stop(self):
        log.info("Shutdown: nenhum browser persistente para fechar")


# ── App ───────────────────────────────────────────────────

class XDeckApp:
    def __init__(self):
        self.bm = BrowserManager()
        self.subscriptions: dict[int | str, dict] = {}
        self.results:       dict[int | str, list] = {}
        self.clients:       set[web.WebSocketResponse] = set()
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_again = False
        self._refresh_started_at: Optional[float] = None
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
            "message": "Preview enviado" if sent else "Sem tweets acima do threshold ou envio de e-mail indisponivel"
        }, status=status)

    async def alert_test_email_handler(self, request):
        scheduler = get_scheduler()
        try:
            data = await request.json()
        except Exception:
            data = {}
        result = scheduler.send_test_email(data if isinstance(data, dict) else {})
        return web.json_response(result, status=200 if result.get("ok") else 400)

    async def operational_mode_handler(self, request):
        mode = get_operational_mode()
        return web.json_response({
            "mode": mode,
            "critical_window": mode == "critical",
            "timezone": "America/Sao_Paulo",
        })

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
                    source = data.get("source") or "manual"
                    if data.get("refresh", True):
                        if source in {"live", "auto"} and not is_critical_window_now():
                            log.info("Auto-refresh WebSocket ignorado fora da janela crítica")
                        else:
                            if source == "manual" and not is_critical_window_now():
                                log.info("Refresh manual solicitado fora da janela crítica")
                            self.schedule_refresh_all(source=source)
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
        label = self._col_label(col_id)
        await self.broadcast({"type":"status","column":col_id,"status":"loading"})
        log.info(f"{label}: RSS antes da busca: {format_rss()}")
        try:
            filtered_query = apply_column_filters(cfg)
            url = build_url(filtered_query, cfg.get("sort","live"))
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
            log.error(f"{label}: ❌ {e}")
            await self.broadcast({"type":"status","column":col_id,
                "status":"error","message":str(e)[:120]})
        finally:
            log.info(f"{label}: RSS depois da busca: {format_rss()}")

    def _refresh_age_seconds(self) -> Optional[float]:
        if self._refresh_started_at is None:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return loop.time() - self._refresh_started_at

    def _log_refresh_state(self, event: str, source: str, level: int = logging.INFO, **extra):
        fields = {
            "event": event,
            "source": source,
            "operational_mode": get_operational_mode(),
            "critical_window": is_critical_window_now(),
            "columns": len(self.subscriptions),
            **extra,
        }
        log.log(level, " ".join(f"{key}={value}" for key, value in fields.items()))

    def _unlock_refresh(self, source: str, task: Optional[asyncio.Task] = None):
        current = task or asyncio.current_task()
        should_unlock = self._refresh_task is current or (task is not None and task is self._refresh_task) or self._refresh_task is None
        if should_unlock:
            self._refresh_task = None
            self._refresh_started_at = None
            self._refresh_again = False
        self._log_refresh_state("refresh_unlocked", source)

    def schedule_refresh_all(self, source: str = "manual"):
        if source in {"live", "auto"} and not is_critical_window_now():
            self._log_refresh_state("refresh_skipped_outside_critical_window", source)
            return

        if self._refresh_task and not self._refresh_task.done():
            age = self._refresh_age_seconds()
            if age is not None and age > REFRESH_GLOBAL_TIMEOUT:
                self._log_refresh_state(
                    "refresh_timeout",
                    source,
                    logging.WARNING,
                    age=f"{age:.1f}s",
                    timeout=f"{REFRESH_GLOBAL_TIMEOUT}s",
                )
                self._refresh_task.cancel()
                self._unlock_refresh(source, task=self._refresh_task)
            else:
                self._refresh_again = True
                self._log_refresh_state("refresh_pending_detected", source)
                log.info("Refresh global já em andamento; novo ciclo enfileirado")
                return

        self._refresh_started_at = asyncio.get_running_loop().time()
        self._refresh_task = asyncio.create_task(self.refresh_all(source=source))

    async def _run_refresh_cycle(self, source: str):
        generation = self._generation
        snapshot = {col_id: cfg.copy() for col_id, cfg in self.subscriptions.items()}
        self._log_refresh_state("refresh_start", source, columns=len(snapshot))
        for col_id in sorted(snapshot, key=str):
            await self.refresh_column(col_id, snapshot[col_id], generation)
            if generation != self._generation:
                log.info("Subscriptions mudaram; interrompendo ciclo antigo")
                break
            await asyncio.sleep(STAGGER_SECONDS)
        if is_critical_window_now():
            get_scheduler().dispatch_scheduled()
        self._log_refresh_state("refresh_end", source, columns=len(snapshot))

    async def _run_refresh_cycle_with_timeout(self, source: str):
        try:
            await asyncio.wait_for(self._run_refresh_cycle(source), timeout=REFRESH_GLOBAL_TIMEOUT)
        except asyncio.TimeoutError:
            self._log_refresh_state(
                "refresh_timeout",
                source,
                logging.WARNING,
                timeout=f"{REFRESH_GLOBAL_TIMEOUT}s",
            )
        except asyncio.CancelledError:
            self._log_refresh_state("refresh_error", source, logging.WARNING, error="cancelled")
            raise
        except Exception as e:
            self._log_refresh_state("refresh_error", source, logging.ERROR, error=repr(e))

    async def refresh_all(self, source: str = "manual"):
        try:
            self._refresh_again = False
            await self._run_refresh_cycle_with_timeout(source)
            if self._refresh_again:
                self._log_refresh_state("refresh_pending_executed", source)
                self._refresh_again = False
                await self._run_refresh_cycle_with_timeout(source)
        finally:
            self._unlock_refresh(source)

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            if self.subscriptions and is_critical_window_now():
                log.info("⏰ Auto-refresh")
                self.schedule_refresh_all(source="auto")

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
    app.router.add_get("/api/operational-mode", deck.operational_mode_handler)
    app.router.add_post("/api/ai/column-summary", deck.column_summary_handler)
    app.on_startup.append(deck.startup)
    app.on_shutdown.append(deck.shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=PORT)
