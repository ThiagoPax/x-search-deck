"""
X Search Deck — servidor sem Playwright.
Chama a API interna do X diretamente via httpx com os cookies do usuário.
RAM: ~30MB (vs 400MB com Chromium)
"""
from __future__ import annotations
import asyncio, json, logging, os, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import httpx
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

PORT             = int(os.environ.get("PORT", 8765))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 90))
STAGGER_SECONDS  = int(os.environ.get("STAGGER_SECONDS", 6))
MAX_TWEETS       = int(os.environ.get("MAX_TWEETS", 25))
X_COOKIES_JSON   = os.environ.get("X_COOKIES_JSON", "")

# Bearer token público do app web do X (estável há anos)
BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I6BeUge7Yfo%3D"
    "Uq7QxtPs2HYwd1AYXpatAHyrjNs6gMU1nHnHnd5a1f96d6o5Hk"
)

# IDs conhecidos do endpoint GraphQL SearchTimeline (tenta em ordem)
SEARCH_QUERY_IDS = [
    "gkjsKepM6gl_HmFWoWKfgg",
    "nK1dw4oV3k4w5TdtcAdSww",
    "Dkre0EFUdurYSGMMXcmLtA",
    "lZ3-uDfeMeFYcSrn0VkNEA",
]

# Features padrão do SearchTimeline
SEARCH_FEATURES = json.dumps({
    "rweb_lists_timeline_redesign_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
})


def normalize_cookies(raw: list[dict]) -> dict[str, str]:
    return {c["name"]: c["value"] for c in raw}


def parse_date(s: str) -> str:
    """Converte datas do Twitter ('Mon Mar 29 20:00:00 +0000 2026') para ISO."""
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).isoformat()
    except Exception:
        return s


def clean_text(text: str) -> str:
    return re.sub(r"\s*https://t\.co/\S+", "", text).strip()


def fmt_num(n) -> str:
    if n is None:
        return ""
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ── Cliente X ─────────────────────────────────────────────

class XClient:
    def __init__(self, cookie_jar: dict[str, str]):
        self.jar = cookie_jar
        self.ct0 = cookie_jar.get("ct0", "")
        self._gql_id: Optional[str] = None  # cache do query ID válido

    def _headers(self) -> dict:
        return {
            "authorization":          f"Bearer {BEARER}",
            "x-csrf-token":           self.ct0,
            "cookie":                 "; ".join(f"{k}={v}" for k, v in self.jar.items()),
            "user-agent":             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "accept":                 "*/*",
            "accept-language":        "pt-BR,pt;q=0.9,en;q=0.8",
            "x-twitter-active-user":  "yes",
            "x-twitter-auth-type":    "OAuth2Session",
            "x-twitter-client-language": "pt",
            "referer":                "https://x.com/search",
            "sec-fetch-dest":         "empty",
            "sec-fetch-mode":         "cors",
            "sec-fetch-site":         "same-origin",
        }

    # ── Método 1: adaptive search (v2 legacy) ─────────────
    async def _search_adaptive(self, client: httpx.AsyncClient, query: str, sort: str) -> list[dict]:
        params = {
            "q":                   query,
            "count":               str(MAX_TWEETS),
            "tweet_mode":          "extended",
            "query_source":        "typed_query",
            "pc":                  "1",
            "spelling_corrections":"1",
        }
        if sort == "live":
            params["f"] = "live"

        r = await client.get(
            "https://x.com/i/api/2/search/adaptive.json",
            headers=self._headers(), params=params
        )
        r.raise_for_status()
        return self._parse_adaptive(r.json())

    def _parse_adaptive(self, data: dict) -> list[dict]:
        tweets_obj = data.get("globalObjects", {}).get("tweets", {})
        users_obj  = data.get("globalObjects", {}).get("users",  {})
        if not tweets_obj:
            return []

        # Ordem da timeline
        ordered = []
        for inst in data.get("timeline", {}).get("instructions", []):
            for entry in inst.get("addEntries", {}).get("entries", []):
                eid = entry.get("entryId", "")
                if not (eid.startswith("sq-I-t-") or eid.startswith("tweet-")):
                    continue
                item = entry.get("content", {}).get("item", {})
                tid = (item.get("content", {}).get("tweet", {}).get("id")
                       or entry.get("sortIndex"))
                if tid:
                    ordered.append(str(tid))
        if not ordered:
            ordered = sorted(tweets_obj, key=lambda x: int(x), reverse=True)

        result = []
        for tid in ordered[:MAX_TWEETS]:
            tw   = tweets_obj.get(str(tid), {})
            user = users_obj.get(str(tw.get("user_id_str", "")), {})
            sn   = user.get("screen_name", "")
            result.append({
                "text":        clean_text(tw.get("full_text", tw.get("text", ""))),
                "author_name": user.get("name", ""),
                "author_handle": f"@{sn}" if sn else "",
                "avatar":      user.get("profile_image_url_https", "").replace("_normal", "_bigger"),
                "url":         f"https://x.com/{sn}/status/{tid}" if sn else "",
                "timestamp":   parse_date(tw.get("created_at", "")),
                "replies":     fmt_num(tw.get("reply_count",   0)),
                "retweets":    fmt_num(tw.get("retweet_count", 0)),
                "likes":       fmt_num(tw.get("favorite_count",0)),
                "views":       "",
            })
        return result

    # ── Método 2: GraphQL SearchTimeline ──────────────────
    async def _search_graphql(self, client: httpx.AsyncClient, query: str, sort: str) -> list[dict]:
        variables = json.dumps({
            "rawQuery":    query,
            "count":       MAX_TWEETS,
            "querySource": "typed_query",
            "product":     "Latest" if sort == "live" else "Top",
        })

        ids_to_try = ([self._gql_id] + SEARCH_QUERY_IDS) if self._gql_id else SEARCH_QUERY_IDS
        seen = set()
        for qid in ids_to_try:
            if not qid or qid in seen:
                continue
            seen.add(qid)
            try:
                r = await client.get(
                    f"https://x.com/i/api/graphql/{qid}/SearchTimeline",
                    headers=self._headers(),
                    params={"variables": variables, "features": SEARCH_FEATURES},
                )
                if r.status_code in (400, 404):
                    log.debug(f"GQL id {qid} → {r.status_code}, tentando próximo")
                    continue
                r.raise_for_status()
                data = r.json()
                tweets = self._parse_graphql(data)
                self._gql_id = qid   # cacheia o que funcionou
                return tweets
            except httpx.HTTPStatusError:
                continue
            except Exception as e:
                log.debug(f"GQL id {qid}: {e}")
                continue
        raise RuntimeError("Todos os query IDs GraphQL falharam")

    def _parse_graphql(self, data: dict) -> list[dict]:
        try:
            instructions = (
                data["data"]["search_by_raw_query"]
                    ["search_timeline"]["timeline"]["instructions"]
            )
        except (KeyError, TypeError):
            return []

        result = []
        for inst in instructions:
            for entry in inst.get("entries", []):
                content = entry.get("content", {})
                if content.get("entryType") == "TimelineTimelineItem":
                    item = content.get("itemContent", {})
                    if item.get("itemType") == "TimelineTweet":
                        tw = self._parse_gql_tweet(
                            item.get("tweet_results", {}).get("result", {})
                        )
                        if tw:
                            result.append(tw)
                # Módulos (múltiplos tweets por entry)
                elif content.get("entryType") == "TimelineTimelineModule":
                    for item_wrap in content.get("items", []):
                        item = item_wrap.get("item", {}).get("itemContent", {})
                        if item.get("itemType") == "TimelineTweet":
                            tw = self._parse_gql_tweet(
                                item.get("tweet_results", {}).get("result", {})
                            )
                            if tw:
                                result.append(tw)
        return result

    def _parse_gql_tweet(self, result: dict) -> Optional[dict]:
        try:
            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet", result)
            legacy = result.get("legacy", {})
            user   = result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
            tid    = legacy.get("id_str", "")
            sn     = user.get("screen_name", "")
            views  = result.get("views", {}).get("count", "")
            return {
                "text":          clean_text(legacy.get("full_text", legacy.get("text", ""))),
                "author_name":   user.get("name", ""),
                "author_handle": f"@{sn}" if sn else "",
                "avatar":        user.get("profile_image_url_https", "").replace("_normal", "_bigger"),
                "url":           f"https://x.com/{sn}/status/{tid}" if sn and tid else "",
                "timestamp":     parse_date(legacy.get("created_at", "")),
                "replies":       fmt_num(legacy.get("reply_count",    0)),
                "retweets":      fmt_num(legacy.get("retweet_count",  0)),
                "likes":         fmt_num(legacy.get("favorite_count", 0)),
                "views":         fmt_num(views) if views else "",
            }
        except Exception:
            return None

    # ── Método principal: tenta adaptive → GraphQL ────────
    async def search(self, query: str, sort: str = "live") -> list[dict]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Tenta adaptive primeiro (mais leve)
            try:
                tweets = await self._search_adaptive(client, query, sort)
                if tweets:
                    log.debug("adaptive.json OK")
                    return tweets
                log.debug("adaptive.json: lista vazia, tentando GraphQL")
            except Exception as e:
                log.debug(f"adaptive.json falhou ({e}), tentando GraphQL")

            # Fallback para GraphQL
            return await self._search_graphql(client, query, sort)


# ── App WebSocket ─────────────────────────────────────────

class XDeckApp:
    def __init__(self):
        self.xclient: Optional[XClient] = None
        self.subscriptions: dict[int, dict] = {}
        self.results:       dict[int, list] = {}
        self.clients:       set[web.WebSocketResponse] = set()

    async def startup(self, app):
        if X_COOKIES_JSON:
            try:
                raw = json.loads(X_COOKIES_JSON)
                jar = normalize_cookies(raw)
                self.xclient = XClient(jar)
                log.info(f"✅ {len(jar)} cookies carregados (sem Playwright)")
                # Teste rápido de autenticação
                await self._auth_check()
            except Exception as e:
                log.error(f"❌ Erro ao carregar cookies: {e}")
        else:
            log.error("❌ X_COOKIES_JSON não definido")

        asyncio.create_task(self._refresh_loop())
        log.info(f"🚀 X Search Deck online — porta {PORT}")

    async def _auth_check(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://x.com/i/api/1.1/account/verify_credentials.json",
                    headers=self.xclient._headers(),
                    params={"skip_status": "1"}
                )
                if r.status_code == 200:
                    data = r.json()
                    log.info(f"✅ Autenticado como @{data.get('screen_name', '?')}")
                else:
                    log.warning(f"⚠️  Auth check retornou {r.status_code}")
        except Exception as e:
            log.warning(f"Auth check: {e}")

    async def shutdown(self, app):
        pass  # nada a fechar — sem Playwright

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
        if not self.xclient:
            await self.broadcast({"type":"status","column":col_id,"status":"error","message":"X_COOKIES_JSON não configurado"})
            return

        await self.broadcast({"type":"status","column":col_id,"status":"loading"})
        try:
            log.info(f"Col {col_id+1}: buscando via API...")
            tweets = await self.xclient.search(cfg["query"], cfg.get("sort","live"))
            self.results[col_id] = tweets
            ts = datetime.now().strftime("%H:%M:%S")
            await self.broadcast({"type":"results","column":col_id,"tweets":tweets,"updated":ts,"count":len(tweets)})
            await self.broadcast({"type":"status","column":col_id,"status":"ok"})
            log.info(f"Col {col_id+1}: ✅ {len(tweets)} tweets")
        except Exception as e:
            log.error(f"Col {col_id+1}: ❌ {e}")
            await self.broadcast({"type":"status","column":col_id,"status":"error","message":str(e)[:120]})

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
