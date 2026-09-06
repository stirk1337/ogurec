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
    ["https://fonts.googleapis.com", "/ogurec/proxy/fonts"],
    ["https://fonts.gstatic.com", "/ogurec/proxy/font-files"],
  ];

  function rewrite(url) {
    if (typeof url !== "string" || !url) return url;
    const absolute = url.startsWith("//") ? `${location.protocol}${url}` : url;
    for (const [from, to] of hosts) {
      if (absolute.startsWith(from)) return to + absolute.slice(from.length);
    }
    return url;
  }

  window.ogurecRewrite = rewrite;

  const src = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  Object.defineProperty(HTMLImageElement.prototype, "src", {
    configurable: true,
    enumerable: true,
    get() {
      return src.get.call(this);
    },
    set(value) {
      src.set.call(this, rewrite(value));
    },
  });

  const setAttribute = HTMLImageElement.prototype.setAttribute;
  HTMLImageElement.prototype.setAttribute = function setAttributeRewritten(name, value) {
    if (String(name).toLowerCase() === "src") value = rewrite(value);
    return setAttribute.call(this, name, value);
  };

  const fetchImpl = window.fetch;
  window.fetch = function fetchRewritten(input, init) {
    if (typeof input === "string") input = rewrite(input);
    else if (input instanceof Request) {
      const next = rewrite(input.url);
      if (next !== input.url) input = new Request(next, input);
    }
    return fetchImpl.call(this, input, init);
  };
})();
