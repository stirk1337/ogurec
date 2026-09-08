import asyncio
import json
import tempfile
from collections import defaultdict
from pathlib import Path

import aiohttp
from aiohttp import web
from loguru import logger

UPSTREAMS = {
    "api": "https://loldle.apimeko.link",
    "cache": "https://cache.loldle.net",
    "ddragon": "https://ddragon.leagueoflegends.com",
    "images": "https://images.loldle.net",
    "audio": "https://audio.loldle.net",
    "audio-i18n": "https://audio-i18n.loldle.net",
    "fonts": "https://fonts.googleapis.com",
    "font-files": "https://fonts.gstatic.com",
}
REPLACEMENTS = {
    "https://loldle.apimeko.link": "/ogurec/proxy/api",
    "https://cache.loldle.net": "/ogurec/proxy/cache",
    "https://ddragon.leagueoflegends.com": "/ogurec/proxy/ddragon",
    "https://images.loldle.net": "/ogurec/proxy/images",
    "https://audio.loldle.net": "/ogurec/proxy/audio",
    "https://audio-i18n.loldle.net": "/ogurec/proxy/audio-i18n",
    "https://fonts.googleapis.com": "/ogurec/proxy/fonts",
    "https://fonts.gstatic.com": "/ogurec/proxy/font-files",
}
AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
}
CLIENT_DIR = Path(__file__).with_name("client")
IFRAME_CHECK = (
    "checkIframe(){try{window.self!==window.top&&window.top.location.origin!==window.location.origin"
    "&&(this.isInIframe=!0)}catch(e){this.isInIframe=!0}}"
)
INDEX_BUNDLE = "js/index.9df01de2d504cd5f2472.1783962704014.js"
ASSET_VERSION = "23"
WORLDS_OFF = (
    (
        "worldsMayhemAvailable(){return this.$store.state.game.worldsMayhemAvailable}",
        "worldsMayhemAvailable(){return !1}",
    ),
    (
        'e.worldsMayhemAvailable?a("div",{staticClass:"button-game"',
        'false?a("div",{staticClass:"button-game"',
    ),
    (
        'this.worldsMayhemAvailable&&e.unshift({name:"worldsMayhem"',
        'false&&e.unshift({name:"worldsMayhem"',
    ),
    (
        'e.worldsMayhemAvailable&&e.isInGame&&!e.isWorldsMayhem?a("div",{staticClass:"worldsMayhemBanner"',
        'false&&e.isInGame&&!e.isWorldsMayhem?a("div",{staticClass:"worldsMayhemBanner"',
    ),
    (
        'a("HubGamesEnd")',
        "e._e()",
    ),
    (
        'a("HubGames")',
        "e._e()",
    ),
)
LOCALE_RU = (
    (
        'new l["a"]({locale:"EN",fallbackLocale:"EN"',
        'new l["a"]({locale:"RU",fallbackLocale:"RU"',
    ),
    (
        'dragonVersion:"12.18.1",locale:"EN"',
        'dragonVersion:"12.18.1",locale:"RU"',
    ),
    (
        'getDefaultLocale(){const e=this.getBrowserLocaleLong();var a=Ka["a"].getTranslateKeyValue(e);'
        "if(null!==a)return a;const t=this.getBrowserLocaleShort();"
        'return a=Ka["a"].getTranslateKeyValue(t),null!==a?t:"EN"}',
        'getDefaultLocale(){return"RU"}',
    ),
)
MEDIA_REWRITE = (
    (
        "imageWithoutDragon(){return this.currentAbility.abilityImageUrl}",
        "imageWithoutDragon(){return(window.ogurecRewrite||function(u){return u})(this.currentAbility.abilityImageUrl)}",
    ),
    (
        ".src=this.url",
        ".src=(window.ogurecRewrite||function(u){return u})(this.url)",
    ),
    (
        "showModal(){return!y[\"a\"].isMobile()&&this.windowWidth<=600&&!this.isHidden}",
        "showModal(){return!1}",
    ),
    (
        "showAppDownloads(){return W[\"a\"].isWeb()&&this.windowWidth<601}",
        "showAppDownloads(){return !1}",
    ),
    (
        "this.isOGV=this.isOggFile&&Gt[\"a\"].isIOS()",
        "this.isOGV=!1",
    ),
)


class ActivityServer:
    def __init__(self, settings):
        self.settings = settings
        self.rooms = defaultdict(set)
        self.states = defaultdict(dict)
        self.on_progress = None
        self.on_idle = None
        self.session = aiohttp.ClientSession(auto_decompress=True)
        self.runner = None
        self.mp3_cache = {}
        self.mp3_lock = asyncio.Lock()

    async def start(self):
        app = web.Application()
        app.router.add_get("/ogurec/activity.js", self.asset)
        app.router.add_get("/ogurec/activity.css", self.asset)
        app.router.add_get("/ogurec/rewrite.js", self.asset)
        app.router.add_get("/ogurec/socket", self.socket)
        app.router.add_post("/ogurec/token", self.token)
        app.router.add_route("*", "/ogurec/proxy/{upstream}/{path:.*}", self.proxy)
        app.router.add_route("*", "/{path:.*}", self.proxy)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.settings.activity_host, self.settings.activity_port).start()

    async def close(self):
        await self.session.close()
        if self.runner:
            await self.runner.cleanup()

    async def asset(self, request):
        name = request.path.rsplit("/", 1)[-1]
        content_type = "text/javascript" if name.endswith(".js") else "text/css"
        return web.Response(body=(CLIENT_DIR / name).read_bytes(), content_type=content_type)

    async def token(self, request):
        if not self.settings.discord_client_secret:
            raise web.HTTPServiceUnavailable(text="DISCORD_CLIENT_SECRET is not configured")
        data = await request.json()
        async with self.session.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": self.settings.discord_client_id,
                "client_secret": self.settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": data.get("code", ""),
                "redirect_uri": "https://127.0.0.1",
            },
        ) as response:
            return web.Response(body=await response.read(), status=response.status, content_type="application/json")

    async def socket(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        room = request.query.get("instance", "")
        if not room:
            await ws.close(code=1008, message=b"Missing instance")
            return ws
        self.rooms[room].add(ws)
        for state in self.states.get(room, {}).values():
            await ws.send_str(json.dumps(state))
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self._publish(room, message.data)
        finally:
            self.rooms[room].discard(ws)
            if not self.rooms[room]:
                self.rooms.pop(room, None)
                if self.on_idle:
                    try:
                        await self.on_idle(room)
                    except Exception:
                        logger.exception("Failed to close LoLdle session")
        return ws

    async def _publish(self, room, payload):
        parsed = None
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("id"):
            if self.on_progress:
                try:
                    merged = await self.on_progress(room, parsed)
                    if isinstance(merged, dict) and merged.get("id"):
                        parsed = merged
                        payload = json.dumps(parsed)
                except Exception:
                    logger.exception("Failed to update LoLdle scoreboard")
            self.states[room][parsed["id"]] = parsed
        for peer in tuple(self.rooms[room]):
            if not peer.closed:
                await peer.send_str(payload)

    async def proxy(self, request):
        upstream_name = request.match_info.get("upstream")
        upstream = UPSTREAMS.get(upstream_name, "https://loldle.net")
        path = request.match_info.get("path", "")
        want_mp3 = path.lower().endswith(".ogg.mp3")
        if want_mp3:
            path = path[:-4]
        url = f"{upstream}/{path}"
        if request.query_string:
            url += f"?{request.query_string}"
        if want_mp3:
            body = await self._mp3_for(url)
            if body:
                return self._media_response(request, body, "audio/mpeg")
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": request.headers.get("User-Agent", "Ogurec Activity"),
            "Referer": "https://loldle.net/",
        }
        if request.content_type:
            headers["Content-Type"] = request.content_type
        async with self.session.request(request.method, url, headers=headers, data=await request.read()) as response:
            body = await response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0]
            if content_type in {"text/html", "text/css", "text/javascript", "application/javascript"}:
                text = body.decode(response.charset or "utf-8")
                for source, target in REPLACEMENTS.items():
                    text = text.replace(source, target)
                text = text.replace(IFRAME_CHECK, "checkIframe(){this.isInIframe=!1}")
                for source, target in WORLDS_OFF:
                    text = text.replace(source, target)
                for source, target in LOCALE_RU:
                    text = text.replace(source, target)
                for source, target in MEDIA_REWRITE:
                    text = text.replace(source, target)
                if content_type == "text/html":
                    text = text.replace(INDEX_BUNDLE, f"{INDEX_BUNDLE}?ogurec={ASSET_VERSION}")
                    injection = (
                        f'<meta name="discord-client-id" content="{self.settings.discord_client_id}">'
                        f'<link rel="stylesheet" href="/ogurec/activity.css?ogurec={ASSET_VERSION}">'
                        f'<script src="/ogurec/rewrite.js?ogurec={ASSET_VERSION}"></script>'
                        '<script>try{if(localStorage.getItem("ogurecLocale")!=="7"){if(!localStorage.getItem("currentLocale")||localStorage.getItem("currentLocale")==="EN")localStorage.setItem("currentLocale","RU");localStorage.setItem("ogurecLocale","7")}}catch(e){}</script>'
                        f'<script type="module" src="/ogurec/activity.js?ogurec={ASSET_VERSION}"></script>'
                    )
                    text = text.replace("</head>", f"{injection}</head>")
                body = text.encode()
            media_type = AUDIO_TYPES.get(Path(path).suffix.lower())
            if media_type:
                return self._media_response(request, body, media_type, status=response.status)
            return web.Response(
                body=body,
                status=response.status,
                content_type=content_type,
                headers={
                    "Cache-Control": "no-store",
                    "Access-Control-Allow-Origin": "*",
                },
            )

    async def _mp3_for(self, url):
        cached = self.mp3_cache.get(url)
        if cached:
            return cached
        async with self.mp3_lock:
            cached = self.mp3_cache.get(url)
            if cached:
                return cached
            async with self.session.get(
                url,
                headers={"User-Agent": "Ogurec Activity", "Referer": "https://loldle.net/"},
            ) as response:
                if response.status >= 400:
                    logger.warning("Quote audio fetch failed {} {}", response.status, url)
                    return None
                ogg = await response.read()
            mp3 = await self._ogg_to_mp3(ogg)
            if not mp3:
                return None
            if len(self.mp3_cache) >= 32:
                self.mp3_cache.pop(next(iter(self.mp3_cache)))
            self.mp3_cache[url] = mp3
            return mp3

    async def _ogg_to_mp3(self, ogg: bytes) -> bytes | None:
        src = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        dst = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        try:
            src.write(ogg)
            src.close()
            dst.close()
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                src.name,
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "5",
                dst.name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.warning("ffmpeg quote transcode failed: {}", err.decode(errors="replace"))
                return None
            return Path(dst.name).read_bytes()
        except (OSError, asyncio.TimeoutError) as error:
            logger.warning("ffmpeg quote transcode error: {}", error)
            return None
        finally:
            Path(src.name).unlink(missing_ok=True)
            Path(dst.name).unlink(missing_ok=True)

    def _media_response(self, request, body: bytes, content_type: str, status: int = 200):
        headers = {
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=86400",
        }
        rng = request.headers.get("Range", "")
        if request.method == "HEAD":
            headers["Content-Length"] = str(len(body))
            return web.Response(status=200, content_type=content_type, headers=headers)
        if status == 200 and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1]
            start_s, _, end_s = spec.partition("-")
            try:
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(body) - 1
            except ValueError:
                start, end = 0, len(body) - 1
            end = min(max(end, start), len(body) - 1)
            start = min(max(start, 0), end)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
            return web.Response(
                body=body[start : end + 1],
                status=206,
                content_type=content_type,
                headers=headers,
            )
        return web.Response(body=body, status=status, content_type=content_type, headers=headers)


async def start_activity_server(settings):
    server = ActivityServer(settings)
    await server.start()
    return server
