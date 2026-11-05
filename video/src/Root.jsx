import { Composition, getStaticFiles, staticFile } from "remotion";
import { Wrapped, FPS, plan } from "./Wrapped";

// Everything comes from public/<year>/, written by `python musicdashboard.py narrate <year>`: narration.json
// (lines with their lengths, which set each card's length) and data.json (the year's review). Music beds are
// public/music/<style>.mp3; a missing one is simply left out (found with getStaticFiles: Remotion's file
// server doesn't answer HEAD requests, so probing with fetch made every bed look missing).
const load = async ({ props }) => {
  const get = (path) => fetch(staticFile(path)).then((r) => r.json());
  const narration = await get(`${props.year}/narration.json`);
  const data = await get(`${props.year}/data.json`);
  const present = new Set(getStaticFiles().map((f) => f.name));
  const music = Object.fromEntries([...new Set(narration.lines.map((l) => l.music))]
    .filter((style) => present.has(`music/${style}.mp3`)).map((style) => [style, true]));
  // Card backgrounds (photos and clips from Pixabay, see CREDITS.md), by card, if visuals.json is there.
  const visuals = present.has("visuals.json") ? await get("visuals.json") : {};
  const timeline = plan(narration.lines);
  return { durationInFrames: timeline.total, props: { ...props, data, music, visuals, timeline } };
};

export const Root = () => (
  <Composition id="Wrapped2025" component={Wrapped} fps={FPS} width={1080} height={1920}
    durationInFrames={FPS * 90} defaultProps={{ year: 2025 }} calculateMetadata={load} />
);
