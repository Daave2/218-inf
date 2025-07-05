# Amazon INF Scraper

This project collects "Item Not Found" (INF) metrics from Amazon Seller Central. Data is logged to `output/inf_items.jsonl`, posted to a chat webhook, and can optionally be emailed as an HTML table.

## Local setup

1. **Python**: install Python 3.11.
2. **Dependencies**: run `pip install -r requirements.txt`.
3. **Configuration**: copy `config.example.json` to `config.json` and fill in
   the values. `thumbnail_size` controls the width of product images in
   emails only (chat messages keep full-size images). If `email_report`
   is enabled, configure the `email_settings` block with your SMTP server
   details.
4. **Run**: execute `python inf.py`. Use `--yesterday` to fetch the previous day's data.

## GitHub Actions

The repository contains `.github/workflows/run-scraper.yml` which runs the scraper on a schedule or manually. Configure the following repository secrets for the workflow:

- `LOGIN_URL`
- `LOGIN_EMAIL`
- `LOGIN_PASSWORD`
- `OTP_SECRET_KEY`
- `INF_WEBHOOK_URL`
- `TARGET_STORE_NAME`
- `TARGET_MERCHANT_ID`
- `TARGET_MARKETPLACE_ID`

If you want the workflow to email the report, also set these optional secrets:

- `EMAIL_REPORT` (set to `true` to enable emailing)
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

The workflow builds `config.json` from these secrets and runs `python inf.py`. Artifacts such as log files and scraped data are uploaded for inspection.


