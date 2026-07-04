import { formatSize } from "../utils/format";
export function summary(bytes: number): string { return `export ${formatSize(bytes)}`; }
