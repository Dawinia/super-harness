// Renderer: talks to main ONLY through the preload bridge, never imports electron/fs/path.
// UI/presentation layer: composes logic (lib) + formatting (utils); the intended
// dependency direction is UI -> lib -> utils.
declare const bridge: { save(data: string): Promise<void> };
import { summary } from "./lib/exporter";
import { formatSize } from "./utils/format";
export function App() { return bridge.save(`${summary(1024)} (${formatSize(1024)})`); }
