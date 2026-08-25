# Daily portfolio news brief

This agent gathers relevant, recent news for the portfolio, removes stories already sent, writes a concise email brief, and sends it at 6:00am AEST each morning.

In addition to the listed equities and ETFs, it tracks Hyperliquid, Aave, HyperLend, Bitcoin, Ethereum, Kinetiq, Tether/USDT, USD Coin/USDC, Ethena USDe, Ethena/ENA, and Rabby Wallet. It also tracks the current top-five constituents of VOO, XLV, and NUKZ; each constituent is clearly labelled with its parent ETF in the email.

It intentionally excludes routine price moves. Material moves can be included when an article describes the reason (earnings, guidance, analyst action, regulation, a major product event, etc.).

## One-time setup

1. Install Python 3.11 or newer, then install the small Google News link-decoder dependency:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt
```
2. Create a Google **App Password** for `liamdwaas@gmail.com` (Google Account > Security > 2-Step Verification > App passwords). Normal Gmail passwords cannot be used for SMTP.
3. Create a local `.env` file from `.env.example` and insert the app password. The file is ignored by Git, so the secret is never committed:

```text
PORTFOLIO_GMAIL_USER=liamdwaas@gmail.com
PORTFOLIO_GMAIL_APP_PASSWORD=your-16-character-app-password
PORTFOLIO_RECIPIENT=liamdewaas@gmail.com
```

Alternatively, set those three values as persistent user environment variables using Windows' Environment Variables settings.

4. Run a preview (this does not send mail):

```powershell
python portfolio_digest.py --dry-run
```

5. Send a one-off test after reviewing the preview:

```powershell
python portfolio_digest.py
```

6. Register the daily task from an elevated or normal PowerShell prompt:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

The installer attempts to run at 6:00am daily using the host's `Australia/Sydney` time zone setting, whether or not you are signed in. If Windows rejects passwordless background registration, open Task Scheduler, select **Daily Portfolio News Brief** > **Properties** > **General**, choose **Run whether user is logged on or not**, and enter your Windows account password directly in the Windows dialog. Windows must be awake at the scheduled time.

## Sources and editorial rules

The agent uses Google News RSS searches targeted at high-quality, freely accessible finance and company sources (including Motley Fool Australia, Yahoo Finance, Reuters, CNBC, Associated Press, ABC News, CoinDesk, The Block, Decrypt, Blockworks, and DL News). It excludes publishers that require a login or normally put articles behind a paywall, including Bloomberg, FT, WSJ, and The Australian. It also queries broad web news for political, regulatory, industry, and macro developments directly relevant to each holding.

Items must be no more than three days old and match meaningful catalysts. Each shortlisted article is opened and its lead paragraphs are scanned; cookie banners, popups, subscription prompts, and disclaimers are discarded. The email provides a 1-2 sentence event-focused summary before the source link. Opinion/speculation such as “which stock is better?” and ordinary price-move pieces are excluded. A durable local history, plus title/content-similarity checks, prevents duplicate or syndicated versions of the same story appearing again.

There is no numerical cap on stories in a brief: every item that passes these editorial, freshness, accessibility, and duplicate checks is eligible for inclusion.

The brief also includes a **Notable price action** section only when BTC, ETH, USDT, USDC, USDe, VOO, NUKZ, or XLV crosses a material three-session move/depeg threshold. It is not a daily price ticker.

The email also has a **Market and major-name developments** section for material economy-wide catalysts (Federal Reserve, Treasury, fiscal policy, tariffs, inflation and similar topics) and material news on large/trending US names. It remains selective rather than a complete market roundup.

The brief is informational, not investment advice. Check primary sources before acting.

## Always-on cloud delivery (GitHub Actions)

The local Windows task cannot run while the computer is powered off. This project therefore includes an always-on workflow at `.github/workflows/daily-portfolio-news.yml`.

1. Create a **private** GitHub repository and push this project to its default branch.
2. In the repository, open **Settings > Secrets and variables > Actions** and add these repository secrets:

   - `PORTFOLIO_GMAIL_USER`
   - `PORTFOLIO_GMAIL_APP_PASSWORD`
   - `PORTFOLIO_RECIPIENT`

3. In **Settings > Actions > General**, set **Workflow permissions** to **Read and write permissions** so the workflow can persist `data/sent_stories.json` and prevent repeats.
4. Run **Daily Portfolio News Brief** once from the Actions tab to validate it.

The workflow runs at **06:00 AEST (20:00 UTC on the previous calendar day)**, including when the computer is off. GitHub schedules workflows on a best-effort basis, so busy periods may delay a run slightly.
