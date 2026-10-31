// Renders docs/data/listening.js (window.LISTENING, written by `musicdashboard.py build`) as the shop.
(function () {
  const site = window.LISTENING;
  if (!site) {
    document.querySelector("main").innerHTML = '<p class="empty">No data yet: run <code>python musicdashboard.py build</code>.</p>';
    return;
  }
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tilt = (i) => `${(((i * 37) % 7) - 3) * 0.7}deg`;
  const fmt = (n) => Number(n).toLocaleString();

  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    return days < 30 ? `${days}d ago` : new Date(iso).toLocaleDateString();
  }

  function cover(src, alt) {
    return src ? `<div class="cover"><img src="${esc(src)}" alt="${esc(alt)}" loading="lazy"></div>`
               : `<div class="cover blank" aria-hidden="true">♪</div>`;
  }

  function sleeve(r, i, sticker) {
    return `<a class="sleeve-card" style="--tilt:${tilt(i)}" href="${esc(r.url || r.album_url)}" target="_blank" rel="noopener">
      ${sticker || ""}${cover(r.image, `${r.album || r.name} cover`)}
      <h3>${esc(r.name)}</h3><p>${esc(r.artist)}${r.year ? ` · ${r.year}` : ""}</p></a>`;
  }

  // Sign and ledger
  document.title = site.title;
  $("title").textContent = site.title;
  const t = site.totals;
  if (t.first) $("est").textContent = `Est. ${new Date(t.first).toLocaleDateString(undefined, { month: "long", year: "numeric" })}`;
  $("ledger").innerHTML = [["Spins", fmt(t.plays)], ["Hours", fmt(t.hours)], ["Artists", fmt(t.artists)], ["Albums", fmt(t.albums)]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

  // On the turntable
  const last = site.recent[0];
  if (last) {
    $("now").innerHTML = `
      <div class="deck"><div class="disc"></div><div class="sleeve">${cover(last.image_large || last.image, `${last.album} cover`)}</div></div>
      <div>
        <p class="now-label">On the turntable · ${esc(ago(last.played_at))}</p>
        <h2 class="now-title"><a href="${esc(last.url)}" target="_blank" rel="noopener">${esc(last.name)}</a></h2>
        <p class="now-artist">${esc(last.artist)}</p>
        <p class="now-album">${esc(last.album)}${last.year ? ` (${last.year})` : ""}</p>
      </div>`;
  }

  $("recent").innerHTML = site.recent.slice(1, 25)
    .map((r, i) => sleeve(r, i, `<span class="sticker tag">${esc(ago(r.played_at))}</span>`)).join("")
    || '<p class="empty">Nothing in the crate yet.</p>';

  const maxPlays = site.top_artists[0]?.plays || 1;
  $("artists").innerHTML = site.top_artists.map((a) => `<li>
      ${a.image ? `<img src="${esc(a.image)}" alt="" loading="lazy">` : '<span class="blank"></span>'}
      <div>${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.name)}</a>` : esc(a.name)}
        <div class="bar" style="width:${(a.plays / maxPlays) * 100}%"></div></div>
      <span class="count">${a.plays}×</span></li>`).join("");

  $("albums").innerHTML = site.top_albums
    .map((a, i) => sleeve({ ...a, name: a.album, url: a.album_url }, i + 3, `<span class="sticker">${a.plays}<br>spins</span>`)).join("");

  $("decades").innerHTML = site.decades.map((d) => `<article class="bin">
      <header><h3>${String(d.decade).slice(2)}s</h3><span>${fmt(d.plays)} spins</span></header>
      <div class="flip">${d.covers.map((c) => `<a href="${esc(c.album_url)}" target="_blank" rel="noopener" title="${esc(`${c.album} — ${c.artist}`)}">
        ${c.image ? `<img src="${esc(c.image)}" alt="${esc(c.album)}" loading="lazy">` : ""}</a>`).join("")}</div>
    </article>`).join("") || '<p class="empty">No release years yet.</p>';

  if (site.episodes.length) {
    $("talk-aisle").hidden = false;
    $("episodes").innerHTML = site.episodes.map((e, i) => {
      const pct = e.duration_ms ? Math.min(100, Math.round((e.progress_ms / e.duration_ms) * 100)) : null;
      return sleeve({ name: e.name, artist: e.show, image: e.image || e.show_image, url: e.url }, i,
        pct !== null ? `<span class="sticker tag">${pct}%</span>` : "");
    }).join("");
  }

  if (site.top_shows?.length) {
    $("shows-aisle").hidden = false;
    $("shows").innerHTML = site.top_shows.map((s, i) => sleeve(
      { name: s.name, artist: `${fmt(s.plays)} episodes`, image: s.image, url: s.url }, i + 5,
      `<span class="sticker">${fmt(s.hours)}<br>hrs</span>`)).join("");
  }

  $("updated").textContent = `Stock taken ${new Date(site.generated).toLocaleString()}`;
})();
