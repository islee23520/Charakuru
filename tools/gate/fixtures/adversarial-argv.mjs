// Windows argv cannot carry NUL (spawn throws ERR_INVALID_ARG_VALUE), so verify-path.mjs launches
// this wrapper as `<runtime> adversarial-argv.mjs <cli> ...args`. It puts "a\0b" into the CLI's argv
// in-process and then imports the real CLI unchanged; the CLI has no test-only branch.
import path from "node:path";
import { pathToFileURL } from "node:url";

const [target, ...rest] = process.argv.slice(2);
const cli = path.resolve(target);
process.argv.splice(1, Infinity, cli, ...rest);
const flag = process.argv.indexOf("--stage");
process.argv[flag === -1 ? 3 : flag + 1] = "a\0b";
await import(pathToFileURL(cli).href);
