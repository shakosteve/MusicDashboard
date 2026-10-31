// Renders docs/data/listening.js (window.LISTENING, written by `musicdashboard.py build`).
(function () {
  const site = window.LISTENING;
  if (!site) {
    document.querySelector("main").innerHTML = '<p class="muted">No data yet: run <code>python musicdashboard.py build</code>.</p>';
    return;
  }
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n).toLocaleString();
  const img = (src, size) => src ? `<img src="${esc(src)}" alt="" loading="lazy" width="${size}" height="${size}">` : '<span class="blank"></span>';
  const link = (url, text) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);

  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    return days < 30 ? `${days}d ago` : new Date(iso).toLocaleDateString();
  }

  // One row: cover, title (linked) over a subtitle, and a value on the right.
  const row = (image, url, title, sub, meta, extra = "") => `<li>${img(image, 40)}
    <div class="text">${link(url, title)}<span class="sub">${esc(sub)}</span>${extra}</div>
    <span class="meta">${esc(meta)}</span></li>`;

  document.title = site.title;
  $("title").textContent = site.title;
  const t = site.totals;
  if (t.first) $("since").textContent = `Since ${new Date(t.first).toLocaleDateString(undefined, { month: "long", year: "numeric" })}`;
  $("totals").innerHTML = [["Plays", t.plays], ["Hours", Math.round(t.hours)], ["Artists", t.artists], ["Albums", t.albums]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${fmt(v)}</dd></div>`).join("");

  const last = site.recent[0];
  if (last) {
    $("now").innerHTML = `${img(last.image_large || last.image, 96)}<div>
      <p class="label">Last played · ${esc(ago(last.played_at))}</p>
      <p class="name">${link(last.url, last.name)}</p>
      <p>${esc(last.artist)}</p>
      <p class="muted">${link(last.album_url, last.album)}${last.year ? ` (${last.year})` : ""}</p></div>`;
  } else {
    $("now").hidden = true;
  }

  $("recent").innerHTML = site.recent.slice(1, 16)
    .map((r) => row(r.image, r.url, r.name, `${r.artist} · ${r.album}`, ago(r.played_at))).join("")
    || '<li class="muted">Nothing yet.</li>';

  const maxArtist = site.top_artists[0]?.plays || 1;
  $("artists").innerHTML = site.top_artists.map((a) =>
    row(a.image, a.url, a.name, "", `${fmt(a.plays)} plays`, `<div class="bar" style="width:${(a.plays / maxArtist) * 100}%"></div>`)).join("");

  $("albums").innerHTML = site.top_albums.slice(0, 10)
    .map((a) => row(a.image, a.album_url, a.album, `${a.artist}${a.year ? ` · ${a.year}` : ""}`, `${fmt(a.plays)} plays`)).join("");

  const maxDecade = Math.max(1, ...site.decades.map((d) => d.plays));
  $("decades").innerHTML = site.decades.map((d) => `<tr>
      <td>${d.decade}s</td><td>${fmt(d.plays)}</td>
      <td><div class="track"><div class="fill" style="width:${(d.plays / maxDecade) * 100}%"></div></div></td>
      <td><div class="covers">${d.covers.slice(0, 5).map((c) => `<a href="${esc(c.album_url)}" target="_blank" rel="noopener" title="${esc(`${c.album} — ${c.artist}`)}">${img(c.image, 28)}</a>`).join("")}</div></td>
    </tr>`).join("") || '<tr><td class="muted">No release years yet.</td></tr>';

  if (site.top_shows?.length) {
    $("shows-section").hidden = false;
    $("shows").innerHTML = site.top_shows.slice(0, 10)
      .map((s) => row(s.image, s.url, s.name, `${fmt(s.plays)} episodes`, `${fmt(s.hours)} hrs`)).join("");
  }

  if (site.episodes.length) {
    $("episodes-section").hidden = false;
    $("episodes").innerHTML = site.episodes.slice(0, 10).map((e) => {
      const pct = e.duration_ms ? Math.min(100, Math.round((e.progress_ms / e.duration_ms) * 100)) : null;
      return row(e.image || e.show_image, e.url, e.name, e.show, pct !== null ? `${pct}%` : "");
    }).join("");
  }

  $("updated").textContent = `Updated ${new Date(site.generated).toLocaleString()}`;
})();
