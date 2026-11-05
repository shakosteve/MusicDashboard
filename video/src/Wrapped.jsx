import { AbsoluteFill, Audio, Img, Loop, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame,
  useVideoConfig } from "remotion";
import { loadFont as loadDisplay } from "@remotion/google-fonts/ArchivoBlack";
import { loadFont as loadSans } from "@remotion/google-fonts/Inter";

// The same look as docs/wrapped.html: Archivo Black over Inter, near-black with one coloured wash per card,
// and a single lime accent. No album art: Spotify's art has to link back to Spotify, and a video can't.
const { fontFamily: DISPLAY } = loadDisplay();
const { fontFamily: SANS } = loadSans("normal", { weights: ["400", "500", "600", "700"], subsets: ["latin"] });
const C = { bg: "#0e0e10", text: "#f5f5f2", muted: "#b9b9b2", faint: "rgba(255,255,255,.14)", accent: "#c4f04a" };
const CLAMP = { extrapolateLeft: "clamp", extrapolateRight: "clamp" };

export const FPS = 30;
const LEAD = 18, BETWEEN = 10, TAIL = 22;  // frames before a card's first line, between its lines, after its last

// Cards in script order (adjacent lines with the same card share it), each line's start within its card,
// and music segments (adjacent cards with the same style share one bed).
export const plan = (lines) => {
  const cards = [];
  lines.forEach((l, n) => {
    const last = cards[cards.length - 1];
    if (last && last.card === l.card) last.lines.push({ ...l, n });
    else cards.push({ card: l.card, music: l.music, lines: [{ ...l, n }] });
  });
  let at = 0;
  for (const c of cards) {
    let t = LEAD;
    for (const l of c.lines) { l.at = t; t += Math.ceil(l.seconds * FPS) + BETWEEN; }
    Object.assign(c, { from: at, frames: t - BETWEEN + TAIL });
    at += c.frames;
  }
  const music = [];
  for (const c of cards) {
    const last = music[music.length - 1];
    if (last && last.style === c.music) last.frames += c.frames;
    else music.push({ style: c.music, from: c.from, frames: c.frames });
  }
  return { cards, music, total: at };
};

const fmt = (n) => Math.round(n).toLocaleString("en-US");
const day = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "long", day: "numeric" });
const ACRONYMS = new Set(["aor", "r&b", "edm", "idm", "nwobhm", "uk", "us", "dj", "ebm"]);
const genreName = (g, title = false) => g.split(/([\s-]+)/).map((w) => ACRONYMS.has(w) ? w.toUpperCase()
  : title && /\w/.test(w) ? w[0].toUpperCase() + w.slice(1) : w).join("");
const clean = (s) => (s || "").replace(/\s+-\s+.*(remaster|version|edit|mix).*$/i, "");

// Animation helpers: a spring from 0 to 1 starting at `delay` frames into the card.
const useIn = (delay = 0) => {
  const { fps } = useVideoConfig();
  return spring({ frame: useCurrentFrame() - delay, fps, config: { damping: 200 } });
};
const Reveal = ({ delay = 0, style, children }) => {
  const p = useIn(delay);
  return <div style={{ opacity: p, transform: `translateY(${(1 - p) * 50}px)`, ...style }}>{children}</div>;
};
const Count = ({ to, delay = 0, frames = 45 }) => {
  const f = useCurrentFrame();
  const v = interpolate(f, [delay, delay + frames], [0, to], { ...CLAMP, easing: (t) => 1 - Math.pow(1 - t, 3) });
  return <>{fmt(v)}</>;
};

const Kicker = ({ children }) => <div style={{ font: `600 34px ${SANS}`, letterSpacing: ".14em", textTransform: "uppercase", color: C.muted, marginBottom: 18 }}>{children}</div>;
const Big = ({ children, size = 230 }) => <div style={{ font: `400 ${size}px/.9 ${DISPLAY}`, letterSpacing: "-.02em" }}>{children}</div>;
const Mid = ({ children, size = 118 }) => <div style={{ font: `400 ${size}px/1 ${DISPLAY}`, letterSpacing: "-.01em" }}>{children}</div>;
const Lede = ({ children }) => <div style={{ font: `400 46px/1.35 ${SANS}`, color: C.muted, marginTop: 28 }}>{children}</div>;
const B = ({ children }) => <b style={{ color: C.text, fontWeight: 600 }}>{children}</b>;
const A = ({ children }) => <span style={{ color: C.accent }}>{children}</span>;

// A card's backdrop: its photo or clip, blurred, darkened and slowly zooming, with the card's colour wash over
// it; or just the wash on black. Clips loop if the card outlasts them.
const Backdrop = ({ v, frames }) => {
  const scale = interpolate(useCurrentFrame(), [0, frames], [1.06, 1.16]);
  const style = { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
    filter: "blur(5px) brightness(.4) saturate(1.1)", transform: `scale(${scale})` };
  return v.kind === "video"
    ? <Loop durationInFrames={Math.round((v.seconds || 8) * FPS)}><OffthreadVideo src={staticFile(v.file)} muted style={style} /></Loop>
    : <Img src={staticFile(v.file)} style={style} />;
};

const Frame = ({ frames, wash, visual, children }) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, 10, frames - 10, frames], [0, 1, 1, 0], CLAMP);
  const [color, at] = wash;
  const base = visual ? "rgba(14,14,16,.35)" : C.bg;
  return (
    <AbsoluteFill style={{ background: C.bg, opacity }}>
      {visual && <Backdrop v={visual} frames={frames} />}
      <AbsoluteFill style={{ background: `radial-gradient(110% 60% at ${at}, ${color}${visual ? "b0" : ""} 0%, transparent 60%), ${base}` }} />
      <div style={{ position: "absolute", inset: "0 90px", display: "flex", flexDirection: "column", justifyContent: "center" }}>{children}</div>
    </AbsoluteFill>
  );
};

// A horizontal bar that grows in: label, bar relative to `max`, value.
const Bar = ({ label, value, max, text, delay, peak }) => {
  const p = useIn(delay);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "300px 1fr 150px", gap: 24, alignItems: "center", font: `500 38px ${SANS}`, opacity: Math.min(1, p * 2) }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <div style={{ height: 30, background: "rgba(255,255,255,.06)", borderRadius: "0 6px 6px 0" }}>
        <div style={{ height: "100%", width: `${(value / max) * 100 * p}%`, background: C.accent, opacity: peak ? 1 : .55, borderRadius: "0 6px 6px 0" }} />
      </div>
      <span style={{ textAlign: "right", color: C.muted, fontVariantNumeric: "tabular-nums" }}>{text}</span>
    </div>
  );
};

const wash = (color, at) => [color, at];

const cards = {
  cover: ({ d }) => (
    <>
      <Reveal><Kicker>Your year in listening</Kicker></Reveal>
      <Reveal delay={6}><div style={{ font: `400 360px/.85 ${DISPLAY}`, letterSpacing: "-.03em" }}>
        <span style={{ color: "transparent", WebkitTextStroke: `5px ${C.text}` }}>{String(d.year).slice(0, 2)}</span><A>{String(d.year).slice(2)}</A>
      </div></Reveal>
      <Reveal delay={18}><Lede><B>{fmt(d.plays)}</B> plays. <B>{fmt(d.artists_count)}</B> artists. <B>{fmt(d.tracks_count)}</B> songs.</Lede></Reveal>
    </>
  ),
  minutes: ({ d }) => {
    const peak = d.months.indexOf(Math.max(...d.months));
    const max = Math.max(...d.months);
    return (
      <>
        <Reveal><Kicker>Time spent listening</Kicker></Reveal>
        <Reveal delay={4}><Big size={200}><Count to={d.minutes} delay={8} /></Big></Reveal>
        <Reveal delay={12}><Lede>minutes of music. That's <B>{(d.minutes / 1440).toFixed(1)} days</B>, and you listened on <B>{d.days_listened} days</B> of the year.</Lede></Reveal>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 14, height: 420, marginTop: 70 }}>
          {d.months.map((m, i) => <MonthBar key={i} i={i} h={m / max} peak={i === peak} />)}
        </div>
      </>
    );
  },
  streak: ({ d }) => {
    const f = useCurrentFrame();
    const year = String(d.year);
    const start = new Date(`${year}-01-01T12:00:00`);
    const cells = Array.from({ length: d.period_days }, (_, i) => {
      const iso = new Date(start.getTime() + i * 864e5).toISOString().slice(0, 10);
      return d.days[iso] || 0;
    });
    const max = Math.max(...cells);
    const shown = interpolate(f, [20, 80], [0, cells.length], CLAMP);
    return (
      <>
        <Reveal><Kicker>Never a quiet day</Kicker></Reveal>
        <Reveal delay={4}><Big><Count to={d.longest_streak.days} delay={6} frames={30} /> <A>days</A></Big></Reveal>
        <Reveal delay={12}><Lede>in a row, ending {day(d.longest_streak.ended)}. Music on <B>{d.days_listened} of {d.period_days}</B> days.</Lede></Reveal>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(19, 1fr)", gap: 8, marginTop: 64 }}>
          {cells.map((m, i) => <div key={i} style={{ aspectRatio: "1", borderRadius: 4,
            background: i < shown && m ? C.accent : C.faint, opacity: i < shown && m ? .25 + .75 * Math.sqrt(m / max) : 1 }} />)}
        </div>
      </>
    );
  },
  artist: ({ d }) => {
    const a = d.top_artists[0];
    return (
      <>
        <Reveal><Kicker>Your top artist</Kicker></Reveal>
        <Reveal delay={4}><div style={{ font: `400 300px/.9 ${DISPLAY}`, color: "transparent", WebkitTextStroke: `5px ${C.accent}` }}>#1</div></Reveal>
        <Reveal delay={10}><Mid size={140}>{a.name}</Mid></Reveal>
        <Reveal delay={18}><Lede>{a.last_year_rank === 1 ? `Number one again, same as ${d.year - 1}.` : a.last_year_rank ? `Up from #${a.last_year_rank} in ${d.year - 1}.` : "Brand new at the top."}</Lede></Reveal>
        <Reveal delay={26}><Stats items={[["Minutes", <Count to={a.minutes} delay={26} />], ["Plays", fmt(a.plays)], ["Of all listening", `${a.share}%`]]} /></Reveal>
      </>
    );
  },
  artists: ({ d, c }) => {
    const rest = d.top_artists.slice(1, 5), max = d.top_artists[0].minutes;
    const step = Math.max(14, Math.floor((c.frames - 60) / 6));
    return (
      <>
        <Reveal><Kicker>Right behind them</Kicker></Reveal>
        <Reveal delay={4}><Mid>Top artists</Mid></Reveal>
        <div style={{ display: "grid", gap: 46, marginTop: 70 }}>
          {rest.map((a, i) => (
            <Reveal key={a.name} delay={20 + i * step}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 28 }}>
                <span style={{ font: `400 64px ${DISPLAY}`, color: C.muted, width: 70 }}>{i + 2}</span>
                <span style={{ font: `700 62px/1.1 ${SANS}` }}>{a.name}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 20, marginLeft: 98, marginTop: 10 }}>
                <div style={{ height: 14, width: `${(a.minutes / max) * 600}px`, background: C.accent, opacity: .6, borderRadius: 7 }} />
                <span style={{ font: `400 34px ${SANS}`, color: C.muted }}>{fmt(a.minutes)} min</span>
              </div>
            </Reveal>
          ))}
        </div>
      </>
    );
  },
  song: ({ d }) => {
    const t = d.top_tracks[0];
    return (
      <>
        <Reveal><Kicker>Your song of the year</Kicker></Reveal>
        <Reveal delay={2} style={{ alignSelf: "center", margin: "30px 0 50px" }}><Record label={clean(t.name)} /></Reveal>
        <Reveal delay={8}><Mid size={150}>{clean(t.name)}</Mid></Reveal>
        <Reveal delay={14}><Lede>{t.artist}</Lede></Reveal>
        <Reveal delay={22}><Stats items={[["Plays", <Count to={t.plays} delay={22} frames={30} />], ["Hours", (t.minutes / 60).toFixed(1)]]} /></Reveal>
      </>
    );
  },
  genres: ({ d }) => {
    const g = d.genres, top = g.top.slice(0, 6);
    return (
      <>
        <Reveal><Kicker>Your sound</Kicker></Reveal>
        <Reveal delay={4}><Mid size={128}>Mostly <A>{genreName(top[0].name)}</A></Mid></Reveal>
        <Reveal delay={12}><Lede><B>{g.count} genres</B> in all. The top one was <B>{top[0].share}%</B> of your listening.</Lede></Reveal>
        <div style={{ display: "grid", gap: 26, marginTop: 70 }}>
          {top.map((x, i) => <Bar key={x.name} label={genreName(x.name, true)} value={x.share} max={top[0].share}
            text={`${x.share}%`} delay={24 + i * 10} peak={i === 0} />)}
        </div>
        <Reveal delay={90}><div style={{ font: `400 28px ${SANS}`, color: C.muted, marginTop: 50 }}>Genres from MusicBrainz</div></Reveal>
      </>
    );
  },
  varied: ({ d }) => {
    const g = d.genres, f = useCurrentFrame();
    const words = g.top.map((x) => genreName(x.name));
    return (
      <>
        <Reveal><Kicker>Your most varied day</Kicker></Reveal>
        <Reveal delay={4}><Big size={190}>{day(g.diverse_day.date)}</Big></Reveal>
        <Reveal delay={12}><Lede><B><Count to={g.diverse_day.count} delay={12} frames={40} /> genres</B> in a single day.</Lede></Reveal>
        <div style={{ position: "relative", height: 620, marginTop: 40 }}>
          {words.map((w, i) => {
            const x = (i % 2) * 50 + (i % 4 < 2 ? 0 : 6), y = Math.floor(i / 2) * 19;  // staggered columns, no overlaps
            const o = interpolate(f, [20 + i * 6, 34 + i * 6], [0, 1], CLAMP);
            return <span key={w} style={{ position: "absolute", left: `${x}%`, top: `${y}%`, opacity: o * (i % 3 ? .55 : 1),
              font: `${i % 3 ? 500 : 700} ${i % 3 ? 40 : 52}px ${SANS}`, color: i % 3 ? C.text : C.accent,
              transform: `translateY(${(1 - o) * 30 - f * .15}px)`, whiteSpace: "nowrap" }}>{w}</span>;
          })}
        </div>
      </>
    );
  },
  decades: ({ d }) => {
    const ds = d.decades.filter((x) => x.minutes >= 60);
    const top = ds.reduce((a, b) => (b.minutes > a.minutes ? b : a));
    return (
      <>
        <Reveal><Kicker>Time travel</Kicker></Reveal>
        <Reveal delay={4}><Mid size={128}>The <A>{String(top.decade).slice(2)}s</A> were your decade</Mid></Reveal>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 22, height: 760, marginTop: 80 }}>
          {ds.map((x, i) => <DecadeBar key={x.decade} x={x} h={x.minutes / top.minutes} peak={x === top} delay={14 + i * 5} />)}
        </div>
      </>
    );
  },
  new: ({ d }) => (
    <>
      <Reveal><Kicker>New to you</Kicker></Reveal>
      <Reveal delay={4}><Big><Count to={d.discoveries.count} delay={6} frames={30} /> <A>artists</A></Big></Reveal>
      <Reveal delay={12}><Lede>you'd never played before {d.year}. The ones that stuck:</Lede></Reveal>
      <Names items={d.discoveries.top.slice(0, 3).map((a) => [a.name, `${fmt(a.minutes)} min`])} delay={30} />
    </>
  ),
  talk: ({ d }) => (
    <>
      <Reveal><Kicker>Not all music</Kicker></Reveal>
      <Reveal delay={4}><Big><Count to={d.podcasts.minutes / 60} delay={6} /> <A>hours</A></Big></Reveal>
      <Reveal delay={12}><Lede>of podcasts on top of the music, across <B>{d.podcasts.shows_count} shows</B>.</Lede></Reveal>
      <Names items={d.podcasts.top.slice(0, 3).map((p) => [p.name, `${fmt(p.minutes / 60)} h`])} delay={30} />
    </>
  ),
  outro: ({ d, c }) => (
    <>
      <Reveal><Kicker>That was {d.year}</Kicker></Reveal>
      <Reveal delay={4}><div style={{ font: `400 300px/.85 ${DISPLAY}`, letterSpacing: "-.03em" }}><A>{d.year}</A></div></Reveal>
      <Reveal delay={(c.lines[1] || c.lines[0]).at}><Mid size={110}>Turn it up for {d.year + 1}.</Mid></Reveal>
      <Reveal delay={(c.lines[1] || c.lines[0]).at + 20}><Lede>The Listening Booth · shakosteve.github.io/MusicDashboard</Lede></Reveal>
    </>
  ),
};

const washes = {
  cover: wash("#3f6212", "50% 0%"), minutes: wash("#14532d", "100% 100%"), streak: wash("#1e3a5f", "0% 0%"),
  artist: wash("#4c1d95", "100% 20%"), artists: wash("#4c1d95", "100% 0%"), song: wash("#9a3412", "0% 100%"),
  genres: wash("#a16207", "100% 50%"), varied: wash("#9d174d", "0% 50%"), decades: wash("#3730a3", "0% 50%"),
  new: wash("#166534", "100% 0%"), talk: wash("#7c2d12", "0% 0%"), outro: wash("#3f6212", "50% 100%"),
};

const MonthBar = ({ i, h, peak }) => {
  const p = useIn(20 + i * 3);
  return (
    <div style={{ flex: 1, height: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", gap: 14 }}>
      <div style={{ width: "100%", height: `${h * 85 * p}%`, background: C.accent, opacity: peak ? 1 : .5, borderRadius: "6px 6px 0 0" }} />
      <span style={{ font: `500 28px ${SANS}`, color: peak ? C.text : C.muted }}>{"JFMAMJJASOND"[i]}</span>
    </div>
  );
};
const DecadeBar = ({ x, h, peak, delay }) => {
  const p = useIn(delay);
  return (
    <div style={{ flex: 1, height: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", gap: 16 }}>
      <span style={{ font: `500 28px ${SANS}`, color: C.muted, opacity: p }}>{fmt(x.minutes / 60)}h</span>
      <div style={{ width: "100%", height: `${h * 80 * p}%`, background: C.accent, opacity: peak ? 1 : .5, borderRadius: "8px 8px 0 0" }} />
      <span style={{ font: `400 36px ${DISPLAY}`, color: peak ? C.text : C.muted }}>{String(x.decade).slice(2)}s</span>
    </div>
  );
};
const Stats = ({ items }) => (
  <div style={{ display: "flex", gap: 70, marginTop: 56 }}>
    {items.map(([k, v]) => (
      <div key={k}><div style={{ font: `600 28px ${SANS}`, letterSpacing: ".12em", textTransform: "uppercase", color: C.muted }}>{k}</div>
        <div style={{ font: `400 84px ${DISPLAY}`, marginTop: 8 }}>{v}</div></div>
    ))}
  </div>
);
const Names = ({ items, delay }) => (
  <div style={{ display: "grid", gap: 30, marginTop: 70 }}>
    {items.map(([name, v], i) => (
      <Reveal key={name} delay={delay + i * 12}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 30, borderBottom: `2px solid ${C.faint}`, paddingBottom: 22 }}>
          <span style={{ font: `700 54px/1.15 ${SANS}` }}>{name}</span>
          <span style={{ font: `400 36px ${SANS}`, color: C.muted, whiteSpace: "nowrap" }}>{v}</span>
        </div>
      </Reveal>
    ))}
  </div>
);
// A spinning record, drawn (not album art), with the song's name on the label.
const Record = ({ label }) => {
  const f = useCurrentFrame();
  return (
    <svg width="560" height="560" viewBox="-100 -100 200 200" style={{ transform: `rotate(${f * 2.4}deg)` }}>
      <circle r="98" fill="#111" stroke="#000" />
      {[90, 82, 74, 66, 58, 50, 42].map((r) => <circle key={r} r={r} fill="none" stroke="#2a2a2a" strokeWidth=".8" />)}
      <path d="M -60 -70 A 92 92 0 0 1 60 -70" stroke="rgba(255,255,255,.12)" strokeWidth="6" fill="none" />
      <circle r="32" fill={C.accent} />
      <text y="4" textAnchor="middle" style={{ font: `400 11px ${DISPLAY}`, fill: C.bg }}>{label.slice(0, 14)}</text>
      <circle r="3" fill={C.bg} />
    </svg>
  );
};

export const Wrapped = ({ year, data, music, visuals = {}, timeline }) => {
  if (!timeline) return null;
  return (
    <AbsoluteFill style={{ background: C.bg, color: C.text, fontFamily: SANS }}>
      {timeline.music.filter((m) => music[m.style]).map((m, i) => (
        <Sequence key={`music-${i}`} from={m.from} durationInFrames={m.frames}>
          <Audio src={staticFile(`music/${m.style}.mp3`)} loop
            volume={(f) => interpolate(f, [0, 20, m.frames - 25, m.frames], [0, .16, .16, 0], CLAMP)} />
        </Sequence>
      ))}
      {timeline.cards.map((c, i) => {
        const Card = cards[c.card];
        return (
          <Sequence key={i} from={c.from} durationInFrames={c.frames}>
            <Frame frames={c.frames} wash={washes[c.card]} visual={visuals[c.card]}><Card d={data} c={c} /></Frame>
            {c.lines.map((l) => (
              <Sequence key={l.n} from={l.at}><Audio src={staticFile(`${year}/${l.file}`)} /></Sequence>
            ))}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
