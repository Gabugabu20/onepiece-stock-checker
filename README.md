# One Piece Playmat stock checker

Checks the product page every 10 minutes with GitHub Actions and sends a Discord message when the buy status changes. Costs nothing.

## Setup (about 5 minutes)

1. **Discord webhook**
   In your Discord server: channel settings > Integrations > Webhooks > New Webhook > Copy Webhook URL.
   In the Discord app, set notifications for that channel to "All Messages" so you get a push on your phone.

2. **GitHub repo**
   Create a new **public** repo and upload `check.py` and `.github/workflows/check.yml` (keep the folder path exactly like this).
   Public repos have unlimited free Actions minutes. A private repo would use about 4300 minutes per month, which is over the free 2000.
   The webhook URL is not in the code, so a public repo is fine.

3. **Secret**
   Repo > Settings > Secrets and variables > Actions > New repository secret
   Name: `DISCORD_WEBHOOK`, value: the webhook URL.

4. **First run**
   Repo > Actions > "Stock check" > Run workflow.
   You should get a "Stock checker is running" message in Discord with the current status.
   Compare it with what you see in the browser. If the buy button status matches, everything works.

## Messages you can get

- 👀 First run, shows the current status
- 🟢 BUYABLE NOW (pings @everyone)
- 🔴 No longer buyable
- 🟡 Something else changed (price, delivery text, ...)
- ⚠️ Page could not be loaded 3 times in a row (e.g. shop is down or blocks the checker)

## Notes

- GitHub sometimes starts scheduled runs a few minutes late. Expect 10 to 20 minutes between checks.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. The script writes a small timestamp to `state.json` once a month to avoid this.
- To check another product, add `PRODUCT_URL: https://...` under `env:` in the workflow.
- Test locally without the web: `HTML_FILE=page.html python3 check.py`
