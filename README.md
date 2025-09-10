# Amazon INF Scraper

This project collects "Item Not Found" (INF) metrics from Amazon Seller Central. Data is logged to `output/inf_items.jsonl`, posted to a chat webhook, and can optionally be emailed as an HTML table.

## Local setup

1. **Python**: install Python 3.11.
2. **Dependencies**: run `pip install -r requirements.txt`.
3. **Configuration**: copy `config.example.json` to `config.json` and fill in
   the values. `thumbnail_size` controls the width of product images in
   emails only (chat messages keep full-size images). If `email_report`
   is enabled, configure the `email_settings` block with your SMTP server
   details. If `enable_stock_lookup` is set, the Morrisons bearer token is
   fetched automatically from a public gist and should not be stored in
   `config.json`.
4. **Run**: execute `python inf.py`. Use `--yesterday` to fetch the previous day's data.

## GitHub Actions

The repository contains two GitHub workflows:

- `.github/workflows/run-scraper.yml` posts INF items to the chat webhook on a schedule and never emails the report.
- `.github/workflows/email-report.yml` sends the daily email report only. It
  always scrapes **yesterday's** data and skips Supabase updates.

Configure the following repository secrets used by the workflows:

- `LOGIN_URL`
- `LOGIN_EMAIL`
- `LOGIN_PASSWORD`
- `OTP_SECRET_KEY`
- `INF_WEBHOOK_URL` (used by `run-scraper.yml`)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `TARGET_STORE_NAME`
- `TARGET_MERCHANT_ID`
- `TARGET_MARKETPLACE_ID`

The email report workflow also requires these SMTP secrets:

- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

The workflow builds `config.json` from these secrets and runs `python inf.py`. Artifacts such as log files and scraped data are uploaded for inspection.

`run-scraper.yml` disables emailing, while `email-report.yml` omits the chat webhook.



## Contributors

- Daave2
- Codex
