const path = require("path");

// Resolve everything relative to this file so a fresh clone (or a folder that
// was moved to a different machine) starts without editing absolute paths.
const python =
  process.platform === "win32"
    ? path.join(__dirname, ".venv", "Scripts", "python.exe")
    : path.join(__dirname, ".venv", "bin", "python");

module.exports = {
  apps: [
    {
      name: "autosecure-bot",
      cwd: __dirname,
      script: python,
      args: "-u bot.py",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
      autorestart: true,
      max_restarts: 10,
      // bot.py exits 0 when another instance already holds logs/bot.lock, so a
      // repeated "pm2 start ecosystem.config.cjs" stops instead of restarting
      // the duplicate copy over and over.
      stop_exit_codes: [0],
    },
  ],
};
