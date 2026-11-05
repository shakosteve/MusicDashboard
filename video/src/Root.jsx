import { Composition, staticFile } from "remotion";
import { Wrapped, FPS, plan } from "./Wrapped";

// Everything comes from public/<year>/, written by `python musicdashboard.py narrate <year>`: narration.json
// (lines with their lengths, which set each card's length) and data.json (the year's review). Music beds are
// public/music/<style>.mp3; a missing one is simply left out.
const load = async ({ props }) => {
  const get = (path) => fetch(staticFile(path)).then((r) => r.json());
  const narration = await get(`${props.year}/narration.json`);
  const data = await get(`${props.year}/data.json`);
  const music = {};
  for (const style of new Set(narration.lines.map((l) => l.music))) {
    const r = await fetch(staticFile(`music/${style}.mp3`), { method: "HEAD" }).catch(() => null);
    if (r && r.ok) music[style] = true;
  }
  const timeline = plan(narration.lines);
  return { durationInFrames: timeline.total, props: { ...props, data, music, timeline } };
};

export const Root = () => (
  <Composition id="Wrapped2025" component={Wrapped} fps={FPS} width={1080} height={1920}
    durationInFrames={FPS * 90} defaultProps={{ year: 2025 }} calculateMetadata={load} />
);
