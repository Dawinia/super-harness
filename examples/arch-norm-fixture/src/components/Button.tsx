import { summary } from "../lib/exporter";
import { formatSize } from "../utils/format";
export function Button() { return `${summary(1)} / ${formatSize(1)}`; }
