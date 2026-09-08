(() => {
  const hosts = [
    ["https://ddragon.leagueoflegends.com", "/ogurec/proxy/ddragon"],
    ["http://ddragon.leagueoflegends.com", "/ogurec/proxy/ddragon"],
    ["https://images.loldle.net", "/ogurec/proxy/images"],
    ["http://images.loldle.net", "/ogurec/proxy/images"],
    ["https://cache.loldle.net", "/ogurec/proxy/cache"],
    ["http://cache.loldle.net", "/ogurec/proxy/cache"],
    ["https://loldle.apimeko.link", "/ogurec/proxy/api"],
    ["http://loldle.apimeko.link", "/ogurec/proxy/api"],
    ["https://audio.loldle.net", "/ogurec/proxy/audio"],
    ["http://audio.loldle.net", "/ogurec/proxy/audio"],
    ["https://audio-i18n.loldle.net", "/ogurec/proxy/audio-i18n"],
    ["http://audio-i18n.loldle.net", "/ogurec/proxy/audio-i18n"],
    ["https://fonts.googleapis.com", "/ogurec/proxy/fonts"],
    ["https://fonts.gstatic.com", "/ogurec/proxy/font-files"],
  ];
  const audioHost = /^(https?:)?\/\/audio(-i18n)?\.loldle\.net/i;

  function rewrite(url) {
    if (typeof url !== "string" || !url) return url;
    const absolute = url.startsWith("//") ? `${location.protocol}${url}` : url;
    let next = url;
    for (const [from, to] of hosts) {
      if (absolute.startsWith(from)) {
        next = to + absolute.slice(from.length);
        break;
      }
    }
    if (/\.ogg(\?|#|$)/i.test(next) && !/\.ogg\.mp3(\?|#|$)/i.test(next)) {
      next = next.replace(/\.ogg(?=(\?|#|$))/i, ".ogg.mp3");
    }
    return next;
  }

  function rewriteValue(value, seen) {
    if (typeof value === "string") return audioHost.test(value) ? rewrite(value) : value;
    if (!value || typeof value !== "object") return value;
    seen = seen || new Set();
    if (seen.has(value)) return value;
    seen.add(value);
    if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i++) value[i] = rewriteValue(value[i], seen);
      return value;
    }
    for (const key of Object.keys(value)) value[key] = rewriteValue(value[key], seen);
    return value;
  }

  function hookSrc(proto) {
    const src = Object.getOwnPropertyDescriptor(proto, "src");
    if (src && src.set) {
      Object.defineProperty(proto, "src", {
        configurable: true,
        enumerable: true,
        get() {
          return src.get.call(this);
        },
        set(value) {
          src.set.call(this, rewrite(value));
        },
      });
    }
    const setAttribute = proto.setAttribute;
    proto.setAttribute = function setAttributeRewritten(name, value) {
      if (String(name).toLowerCase() === "src") value = rewrite(value);
      return setAttribute.call(this, name, value);
    };
  }

  window.ogurecRewrite = rewrite;

  hookSrc(HTMLImageElement.prototype);
  hookSrc(HTMLMediaElement.prototype);
  hookSrc(HTMLSourceElement.prototype);

  const NativeAudio = window.Audio;
  function Audio(src) {
    return src == null || src === "" ? new NativeAudio() : new NativeAudio(rewrite(String(src)));
  }
  Audio.prototype = NativeAudio.prototype;
  window.Audio = Audio;

  const fetchImpl = window.fetch;
  window.fetch = function fetchRewritten(input, init) {
    if (typeof input === "string") input = rewrite(input);
    else if (input instanceof Request) {
      const next = rewrite(input.url);
      if (next !== input.url) input = new Request(next, input);
    }
    return fetchImpl.call(this, input, init);
  };

  const xhrOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function openRewritten(method, url, ...rest) {
    if (typeof url === "string") url = rewrite(url);
    return xhrOpen.call(this, method, url, ...rest);
  };

  const jsonParse = JSON.parse;
  JSON.parse = function parseRewritten(text, reviver) {
    return rewriteValue(jsonParse.call(this, text, reviver));
  };
})();
