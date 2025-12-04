"""Automation utilities for Sunflower Land resource harvesting.

This module fetches farm state from the community API, structures the
resource data into categories with coordinates, and offers a Playwright-based
routine to log in and perform harvesting actions with retries/backoff.
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests
from playwright.async_api import Locator, Page, async_playwright

API_URL = "https://api.sunflower-land.com/community/farms/5005859234406467"


@dataclass
class ResourceNode:
    """Represents a harvestable resource with a coordinate."""

    category: str
    name: str
    coordinates: Tuple[int, int]
    clicks_required: int = 1
    ready_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FarmSnapshot:
    """Structured data parsed from the farm API response."""

    resources: Dict[str, List[ResourceNode]]
    inventory: Dict[str, Any]


@dataclass
class AutomationConfig:
    """Configuration for network and browser automation."""

    api_key: str = os.getenv("SUNFLOWER_API_KEY", "")
    email: str = os.getenv("SUNFLOWER_EMAIL", "")
    password: str = os.getenv("SUNFLOWER_PASSWORD", "")
    profile_dir: str = os.getenv("SUNFLOWER_PROFILE", "./.sunflower-profile")
    headless: bool = os.getenv("SUNFLOWER_HEADLESS", "true").lower() == "true"
    request_timeout: int = int(os.getenv("SUNFLOWER_TIMEOUT", "15"))
    max_retries: int = int(os.getenv("SUNFLOWER_RETRIES", "3"))
    backoff_base: float = float(os.getenv("SUNFLOWER_BACKOFF", "1.5"))
    action_delay: float = float(os.getenv("SUNFLOWER_ACTION_DELAY", "0.4"))
    navigation_timeout: int = int(os.getenv("SUNFLOWER_NAVIGATION_TIMEOUT", "30000"))
    axe_selector: str = os.getenv("SUNFLOWER_AXE_SELECTOR", "text=Axe")
    coin_selector: str = os.getenv("SUNFLOWER_COIN_SELECTOR", "[data-testid=coin-balance]")
    login_email_selector: str = os.getenv("SUNFLOWER_EMAIL_SELECTOR", "input[type=email]")
    login_password_selector: str = os.getenv("SUNFLOWER_PASSWORD_SELECTOR", "input[type=password]")
    login_submit_selector: str = os.getenv("SUNFLOWER_LOGIN_SELECTOR", "button:has-text('Login')")
    chop_button_selector: str = os.getenv("SUNFLOWER_CHOP_SELECTOR", "button:has-text('Chop')")
    buy_axe_selector: str = os.getenv("SUNFLOWER_BUY_AXE_SELECTOR", "button:has-text('Buy axe')")
    farm_url: str = os.getenv("SUNFLOWER_FARM_URL", "https://sunflower-land.com/play/")


RESOURCE_KEYS = {
    "trees": "tree",
    "stones": "stone",
    "iron": "iron",
    "gold": "gold",
    "boulders": "boulder",
    "fruitPatches": "fruit",
}


def _extract_coordinates(node: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    coords = node.get("coordinates")
    if isinstance(coords, dict) and {"x", "y"}.issubset(coords):
        return int(coords["x"]), int(coords["y"])
    if {"x", "y"}.issubset(node):
        return int(node["x"]), int(node["y"])
    return None


def _extract_clicks(node: Dict[str, Any]) -> int:
    for key in ("amount", "chopsLeft", "hitsLeft", "harvestsLeft"):
        if key in node and isinstance(node[key], int):
            return max(1, node[key])
    return 1


def fetch_farm_state(config: AutomationConfig) -> Dict[str, Any]:
    """Fetch farm data from the community API with retries."""

    headers = {"x-api-key": config.api_key}
    last_error: Optional[Exception] = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = requests.get(API_URL, headers=headers, timeout=config.request_timeout)
            response.raise_for_status()
            logging.info("Fetched farm state on attempt %s", attempt)
            return response.json()
        except Exception as exc:  # requests raises multiple exception types
            last_error = exc
            sleep_time = config.backoff_base ** attempt
            logging.warning("Attempt %s failed (%s); retrying in %.2fs", attempt, exc, sleep_time)
            time.sleep(sleep_time)
    raise RuntimeError(f"Failed to fetch farm state after {config.max_retries} attempts: {last_error}")


def parse_resources(payload: Dict[str, Any]) -> FarmSnapshot:
    """Parse farm payload into categories with coordinates."""

    resources: Dict[str, List[ResourceNode]] = defaultdict(list)
    inventory = payload.get("inventory", payload.get("balances", {}))
    game_data = payload.get("game", payload)

    for key, category in RESOURCE_KEYS.items():
        nodes = game_data.get(key, [])
        if not isinstance(nodes, list):
            continue
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            coords = _extract_coordinates(node)
            if coords is None:
                logging.debug("Skipping %s entry %s without coordinates", key, idx)
                continue
            clicks = _extract_clicks(node)
            ready_at = node.get("readyAt") or node.get("ready")
            resources[category].append(
                ResourceNode(
                    category=category,
                    name=node.get("name", category.title()),
                    coordinates=coords,
                    clicks_required=clicks,
                    ready_at=str(ready_at) if ready_at is not None else None,
                    raw=node,
                )
            )
    return FarmSnapshot(resources=dict(resources), inventory=inventory)


class BrowserAutomation:
    """Playwright automation for harvesting resources."""

    def __init__(self, config: AutomationConfig) -> None:
        self.config = config

    async def __aenter__(self) -> "BrowserAutomation":
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.config.profile_dir,
            headless=self.config.headless,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.browser.close()
        await self.playwright.stop()

    async def run(self, snapshot: FarmSnapshot) -> None:
        page = await self.browser.new_page()
        await self._login(page)
        await self._harvest_resources(page, snapshot)
        await page.close()

    async def _login(self, page: Page) -> None:
        logging.info("Navigating to farm page")
        await page.goto(self.config.farm_url, timeout=self.config.navigation_timeout)
        await page.wait_for_timeout(1000)

        if self.config.email and self.config.password:
            logging.info("Performing credential login")
            await self._fill_and_submit_login(page)
        else:
            logging.info("No credentials provided; assuming persisted session")

    async def _fill_and_submit_login(self, page: Page) -> None:
        await self._fill_input(page.locator(self.config.login_email_selector), self.config.email)
        await self._fill_input(page.locator(self.config.login_password_selector), self.config.password)
        await self._click_with_retry(page.locator(self.config.login_submit_selector))
        await page.wait_for_timeout(int(self.config.action_delay * 1000))
        logging.info("Login submitted")

    async def _harvest_resources(self, page: Page, snapshot: FarmSnapshot) -> None:
        trees = snapshot.resources.get("tree", [])
        if trees:
            await self._ensure_axes(page, len(trees))
        for resource in trees:
            await self._perform_clicks(page, resource)

        for category, nodes in snapshot.resources.items():
            if category == "tree":
                continue
            for resource in nodes:
                await self._perform_clicks(page, resource)

    async def _ensure_axes(self, page: Page, required: int) -> None:
        available = await self._read_int_from_locator(page.locator(self.config.axe_selector))
        coins = await self._read_int_from_locator(page.locator(self.config.coin_selector))
        logging.info("Axes available: %s; required for trees: %s", available, required)
        logging.info("Coin balance before purchases: %s", coins)
        if available >= required:
            return
        purchase_needed = required - available
        logging.info("Purchasing %s axes", purchase_needed)
        if coins <= 0:
            logging.warning("No coins available; cannot purchase axes")
            return
        for _ in range(purchase_needed):
            await self._click_with_retry(page.locator(self.config.buy_axe_selector))
            await page.wait_for_timeout(int(self.config.action_delay * 1000))

    async def _perform_clicks(self, page: Page, resource: ResourceNode) -> None:
        logging.info(
            "Harvesting %s at %s with %s clicks", resource.category, resource.coordinates, resource.clicks_required
        )
        x, y = resource.coordinates
        for click in range(resource.clicks_required):
            await self._click_coordinate(page, x, y, label=f"{resource.category} click {click + 1}")
            await page.wait_for_timeout(int(self.config.action_delay * 1000))

    async def _click_coordinate(self, page: Page, x: int, y: int, label: str) -> None:
        await self._retry_action(lambda: page.mouse.click(x, y), f"click at ({x}, {y}) for {label}")

    async def _click_with_retry(self, locator: Locator) -> None:
        await self._retry_action(locator.click, f"click locator {locator}")

    async def _fill_input(self, locator: Locator, value: str) -> None:
        await self._retry_action(lambda: locator.fill(value), f"fill input for locator {locator}")

    async def _retry_action(self, action, description: str) -> None:
        delay = self.config.backoff_base
        for attempt in range(1, self.config.max_retries + 1):
            try:
                await action()
                logging.debug("Succeeded: %s", description)
                return
            except Exception as exc:
                logging.warning("Attempt %s failed for %s: %s", attempt, description, exc)
                await asyncio.sleep(delay)
                delay *= self.config.backoff_base
        raise RuntimeError(f"Action failed after retries: {description}")

    async def _read_int_from_locator(self, locator: Locator) -> int:
        try:
            content = await locator.inner_text()
            digits = "".join(ch for ch in content if ch.isdigit())
            return int(digits) if digits else 0
        except Exception as exc:
            logging.warning("Could not read locator text (%s): %s", locator, exc)
            return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    config = AutomationConfig()

    if not config.api_key:
        raise RuntimeError("SUNFLOWER_API_KEY is required to query the farm API.")

    logging.info("Fetching farm state from community API")
    payload = fetch_farm_state(config)
    snapshot = parse_resources(payload)
    logging.info("Parsed resources: %s", json.dumps({k: len(v) for k, v in snapshot.resources.items()}))

    if config.email and config.password:
        logging.info("Launching browser automation")
        asyncio.run(run_browser(snapshot, config))
    else:
        logging.info("No email/password provided; skipping browser automation")


def resources_to_actions(snapshot: FarmSnapshot) -> Dict[str, List[Tuple[int, int]]]:
    """Convert parsed resources to coordinate tuples grouped by category."""

    return {
        category: [node.coordinates for node in nodes]
        for category, nodes in snapshot.resources.items()
    }


async def run_browser(snapshot: FarmSnapshot, config: AutomationConfig) -> None:
    async with BrowserAutomation(config) as automation:
        await automation.run(snapshot)


if __name__ == "__main__":
    main()
