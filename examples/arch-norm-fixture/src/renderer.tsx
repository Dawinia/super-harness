// Renderer: talks to main ONLY through the preload bridge, never imports electron/fs/path.
declare const bridge: { save(data: string): Promise<void> };
import { formatSize } from "./utils/format";
export function App() { return bridge.save(formatSize(1024)); }
