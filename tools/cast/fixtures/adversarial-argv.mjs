const marker = process.env.ADVERSARIAL_ARG;
const flag = process.argv.indexOf("--stage");
const target = flag === -1 ? 3 : flag + 1;
if (marker === "-") process.argv[target] = "";
if (marker === "nul") process.argv[target] = "a\0b";
