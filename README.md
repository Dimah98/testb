# Sunflower Land automation helper

This repository contains a small automation helper for fetching resource data from the
Sunflower Land community API and optionally driving Playwright to harvest resources.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. Export the required environment variables (see below).
4. Run the helper:

   ```bash
   python src/sunflower_automation.py
   ```

## Configuration

The script is configured via environment variables to avoid storing credentials in code:

- `SUNFLOWER_API_KEY` (**required**): API key for `https://api.sunflower-land.com/community/farms/5005859234406467`.
- `SUNFLOWER_EMAIL` / `SUNFLOWER_PASSWORD`: Account credentials for browser login. Omit to rely on an already-authenticated profile.
- `SUNFLOWER_PROFILE`: Path to a dedicated Playwright persistent profile directory (default: `./.sunflower-profile`).
- `SUNFLOWER_HEADLESS`: `true`/`false` toggle for headless mode (default: `true`).
- `SUNFLOWER_TIMEOUT`: HTTP timeout in seconds (default: `15`).
- `SUNFLOWER_RETRIES`: Number of retries for HTTP and UI actions (default: `3`).
- `SUNFLOWER_BACKOFF`: Backoff base used for exponential retries (default: `1.5`).
- `SUNFLOWER_ACTION_DELAY`: Delay (seconds) between UI clicks (default: `0.4`).
- Selector overrides such as `SUNFLOWER_AXE_SELECTOR`, `SUNFLOWER_COIN_SELECTOR`, `SUNFLOWER_LOGIN_SELECTOR`, etc., for UI tuning.

## What the script does

1. Sends an authenticated GET request to the farm endpoint, logging retries and timeouts.
2. Parses inventories and coordinates into categorized resource lists (trees, stones, iron, gold, boulders, fruit patches).
3. Converts parsed data into action-ready coordinate tuples for sequencing.
4. Uses Playwright with a persistent profile to:
   - Load `https://sunflower-land.com/play/` and log in if credentials are provided.
   - Check available axes against tree count, buy more axes when needed, and pause between clicks.
   - Iterate over all parsed resources, clicking the required number of times per coordinate with retry/backoff.

## Usage notes and safety

- **Rate limiting:** The community API may enforce limits; retries use exponential backoff to avoid aggressive polling.
- **Account risk:** Automation can violate game policies. Use a dedicated account/profile and proceed at your own risk.
- **UI fragility:** Selectors can change. Override them with environment variables if elements are not found.
- **Delays:** Tune `SUNFLOWER_ACTION_DELAY` and `SUNFLOWER_BACKOFF` to match your connection/latency.
- **Storage:** The persistent profile directory keeps session data; isolate it from personal profiles.
