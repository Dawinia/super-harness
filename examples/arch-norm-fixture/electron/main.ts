import { app } from "electron";
import { readFileSync } from "fs";
export function start() { app.whenReady().then(() => readFileSync("/tmp/x")); }
