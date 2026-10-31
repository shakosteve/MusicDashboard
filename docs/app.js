// Car-audio design: renders window.LISTENING (written by `musicdashboard.py build`) as a head unit,
// a mixtape J-card, 8-tracks, cassettes, a graphic equalizer and an AM dial.
(function () {
  const site = window.LISTENING;
  const base = window.BOOTH_BASE || "";
  if (!site) {
    document.querySelector("main").innerHTML = '<p class="dek">No data yet: run <code>python musicdashboard.py build</code>.</p>';
    return;
  }
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n).toLocaleString();
  const img = (src, alt = "") => src ? `<img src="${esc(src)}" alt="${esc(alt)}" loading="lazy">` : '<span class="blank"></span>';
  const link = (url, text) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text);
  const short = (n) => n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} hr ago`;
    const days = Math.round(hrs / 24);
    return days < 30 ? `${days} d ago` : new Date(iso).toLocaleDateString();
  }

  document.title = site.title;
  $("title").textContent = site.title;
  const t = site.totals;
  if (t.first) $("since").textContent = `Since ${new Date(t.first).getFullYear()}`;
  if ((window.WRAPPED_YEARS || []).length) $("preset-year").href = `${base}wrapped.html`;
  else $("preset-year").remove();

  // Head unit: the last played track in the display, its cover in the slot.
  const last = site.recent[0];
  if (last) {
    $("now-art").href = last.album_url || last.url;
    $("now-art").innerHTML = img(last.image_large || last.image, last.album);
    $("vfd-title").innerHTML = link(last.url, `${last.name} · ${last.artist}`);
    $("vfd-sub").innerHTML = `${link(last.album_url, last.album)}${last.year ? ` (${last.year})` : ""}`;
    $("vfd-ago").textContent = ago(last.played_at).toUpperCase();
    // Long titles scroll across the display, the way a head unit does it.
    requestAnimationFrame(() => {
      const box = document.querySelector(".vfd-title");
      const over = box.firstElementChild.scrollWidth - box.clientWidth;
      if (over > 4 && !reduce) {
        box.style.setProperty("--shift", `${-over}px`);
        box.style.setProperty("--dur", `${Math.max(6, over / 30)}s`);
        box.classList.add("scroll");
      }
    });
  }

  // Odometer: six drums per counter, rolled into place on load.
  $("totals").innerHTML = [["Plays", t.plays], ["Hours", Math.round(t.hours)], ["Artists", t.artists], ["Albums", t.albums]]
    .map(([label, value]) => {
      const digits = String(Math.round(value)).padStart(6, "0");
      const lead = digits.length - String(Math.round(value)).length;
      return `<div class="meter" role="img" aria-label="${label}: ${fmt(value)}"><span class="label">${label}</span>
        <span class="drums" aria-hidden="true">${[...digits].map((d, i) => `<span class="drum${i < lead ? " lead" : ""}">
          <span class="strip" data-d="${d}" style="--delay:${(i * 0.08).toFixed(2)}s">${"0123456789".split("").map((n) => `<span>${n}</span>`).join("")}</span></span>`).join("")}</span></div>`;
    }).join("");
  requestAnimationFrame(() => requestAnimationFrame(() =>
    document.querySelectorAll(".strip").forEach((s) => s.style.setProperty("--d", s.dataset.d))));

  // Side A / Side B: the next 16 plays, written on the insert.
  const track = (r) => `<li>${img(r.image)}<div class="t">${link(r.url, r.name)}<span>${esc(r.artist)}</span></div>
    <span class="when">${esc(ago(r.played_at))}</span></li>`;
  const recent = site.recent.slice(1, 17);
  $("side-a-list").innerHTML = recent.slice(0, 8).map(track).join("") || "<li>Blank tape.</li>";
  $("side-b-list").innerHTML = recent.slice(8, 16).map(track).join("");
  $("side-b-list").style.setProperty("--start", 8);

  // 8-tracks: top artists, with a strip of tape measured against the top artist.
  const maxArtist = site.top_artists[0]?.plays || 1;
  $("artists").innerHTML = site.top_artists.map((a, i) => `<div class="eight">
      <div class="label">${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener" aria-label="${esc(a.name)} on Spotify">${img(a.image)}</a>` : img(a.image)}
        <div><span class="no">Nº ${String(i + 1).padStart(2, "0")}</span>${a.url
          ? `<a class="name" href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.name)}</a>`
          : `<span class="name">${esc(a.name)}</span>`}
          <span class="plays">${fmt(a.plays)} plays</span>
          <div class="tape" title="${fmt(a.plays)} plays"><i style="width:${(a.plays / maxArtist) * 100}%"></i></div></div>
      </div>
      <div class="ribs" aria-hidden="true"></div>
    </div>`).join("");

  // Cassettes: top albums. The art is the J-card in the case; the tape slides out on hover.
  $("albums").innerHTML = site.top_albums.slice(0, 12).map((a) => `<a class="cassette" href="${esc(a.album_url || a.url)}" target="_blank" rel="noopener">
      <div class="case"><span class="spine">${esc(a.artist)} — ${esc(a.album)}</span>${img(a.image_large || a.image, a.album)}</div>
      <div class="shell"><div class="lbl">
        <div class="row"><span class="side-letter">A</span><span class="album">${esc(a.album)}</span></div>
        <div class="meta"><span>${esc(a.artist)}</span><span>${a.year || ""}</span></div>
        <div class="window" aria-hidden="true"><span class="reel"></span><span class="reel"></span></div>
        <div class="meta plays"><span>${fmt(a.plays)} plays</span></div>
      </div></div>
    </a>`).join("");

  // Coming back for seconds: the songs replayed most, from the export (see replay_stats in musicdashboard.py).
  const rp = site.replays;
  if (rp?.top?.length) {
    $("seconds").hidden = false;
    $("seconds-dek").textContent = `Shuffle on, hands off: only about 1 play in ${Math.round(100 / rp.share)} is a replay. These are the songs you rewound anyway.`;
    const how = (r) => [[r.run_back, "ran it back"], [r.went_back, "went back"], [r.right_after, "right after"]]
      .filter(([n]) => n).map(([n, label]) => `${fmt(n)} ${label}`).join(" · ");
    $("rewinds").innerHTML = rp.top.map((r, i) => `<li><span class="n">${i + 1}</span>${img(r.image, r.album)}
      <div class="t">${link(r.url, r.name)}<span class="by">${esc(r.artist)} · ${fmt(r.plays)} plays</span><span class="how">${how(r)}</span></div>
      <div class="counter" title="${fmt(r.total)} replays"><b><i aria-hidden="true">◀︎◀︎</i>${r.total}</b><span>replays</span></div></li>`).join("");
    $("seconds-note").textContent = `From the streaming history through ${new Date(rp.through).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}. `
      + "Ran it back: one play that lasted past the song's length, from dragging back to the start or repeat-one. "
      + "Went back: the back button or picking it again within a few songs. Right after: finished, then played again.";
  }

  // Equalizer: one band per release decade, twelve LEDs tall. A placeholder 1900 release year isn't a decade.
  const decades = site.decades.filter((d) => d.decade >= 1910 && d.plays > 0);
  const maxDecade = Math.max(1, ...decades.map((d) => d.plays));
  const top = decades.reduce((a, b) => (b.plays > a.plays ? b : a), decades[0]);
  $("bands").style.setProperty("--n", decades.length);
  $("bands").innerHTML = decades.map((d) => {
    const lit = Math.max(1, Math.round((d.plays / maxDecade) * 12));
    return `<button class="band${d === top ? " sel" : ""}" data-decade="${d.decade}" title="${d.decade}s: ${fmt(d.plays)} plays" aria-label="${d.decade}s: ${fmt(d.plays)} plays">
      <span class="v">${short(d.plays)}</span>
      <span class="leds">${Array.from({ length: 12 }, (_, i) => `<i${i < lit ? ' class="on"' : ""}></i>`).join("")}</span>
      <span class="hz">${String(d.decade).slice(2)}s</span></button>`;
  }).join("");
  const tune = (decade) => {
    const d = decades.find((x) => x.decade === decade);
    document.querySelectorAll(".band").forEach((b) => b.classList.toggle("sel", Number(b.dataset.decade) === decade));
    $("tuned").innerHTML = `<p>Tuned to the <b>${d.decade}s</b> · ${fmt(d.plays)} plays · ${Math.round((d.plays / decades.reduce((s, x) => s + x.plays, 0)) * 100)}% of everything</p>
      <div class="covers">${d.covers.map((c) => `<a href="${esc(c.album_url || c.url)}" target="_blank" rel="noopener" title="${esc(`${c.album} — ${c.artist}${c.year ? ` (${c.year})` : ""}`)}">${img(c.image, c.album)}</a>`).join("")}</div>`;
  };
  $("bands").addEventListener("click", (e) => {
    const b = e.target.closest(".band");
    if (b) tune(Number(b.dataset.decade));
  });
  if (top) tune(top.decade);

  // AM dial: top shows as presets; pointing at one swings the needle to it.
  if (site.top_shows?.length) {
    $("am").hidden = false;
    const shows = site.top_shows.slice(0, 8);
    const spot = (i) => `${8 + (i / Math.max(1, shows.length - 1)) * 84}%`;
    $("shows").innerHTML = shows.map((s, i) => `<li data-i="${i}"><span class="ch">${i + 1}</span>${img(s.image)}
      <div class="t">${link(s.url, s.name)}<span>${fmt(s.plays)} episodes</span></div><span class="v">${fmt(s.hours)} h</span></li>`).join("");
    const needle = $("needle");
    needle.style.left = spot(0);
    $("shows").addEventListener("pointerover", (e) => {
      const li = e.target.closest("li");
      if (li) needle.style.left = spot(Number(li.dataset.i));
    });
    $("shows").addEventListener("focusin", (e) => {
      const li = e.target.closest("li");
      if (li) needle.style.left = spot(Number(li.dataset.i));
    });
  } else {
    $("preset-am").remove();
  }
  if (site.episodes?.length) {
    $("am").hidden = false;
    $("episodes-wrap").hidden = false;
    $("episodes").innerHTML = site.episodes.slice(0, 8).map((e) => {
      const pct = e.duration_ms ? Math.min(100, Math.round((e.progress_ms / e.duration_ms) * 100)) : null;
      return `<li>${img(e.image || e.show_image)}<div class="t">${link(e.url, e.name)}<span>${esc(e.show)}</span>
        ${pct !== null ? `<div class="progress" title="${pct}% played"><i style="width:${pct}%"></i></div>` : ""}</div>
        <span class="v">${pct !== null ? `${pct}%` : ""}</span></li>`;
    }).join("");
  }

  $("updated").textContent = `Updated ${new Date(site.generated).toLocaleString()}`;
})();
