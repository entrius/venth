"""
Synth API Client — Dual-mode SDK wrapper.

Automatically uses mock data when no API key is available (contributor mode),
and hits the real Synth API when SYNTH_API_KEY is set (maintainer/CI mode).

Usage:
    from synth_client import SynthClient

    client = SynthClient()  # auto-detects mode from SYNTH_API_KEY env var
    data = client.get_prediction_percentiles("BTC", horizon="24h")
"""

import json
import os
import warnings
from pathlib import Path

try:
    import requests

    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

BASE_URL = "https://api.synthdata.co"

# All supported assets
SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
SUPPORTED_HORIZONS = ["1h", "24h"]


class SynthClient:
    """
    Dual-mode client for the Synth API.

    - If SYNTH_API_KEY is set → makes real API calls.
    - If SYNTH_API_KEY is not set → loads from mock_data/ directory.
    """

    def __init__(self, api_key: str | None = None, mock_data_dir: str | None = None):
        """
        Initialize the Synth client.

        Args:
            api_key: Synth API key. If None, reads from SYNTH_API_KEY env var.
                     If still None, falls back to mock mode.
            mock_data_dir: Path to mock data directory. Defaults to mock_data/
                          relative to the repo root.
        """
        self.api_key = api_key or os.environ.get("SYNTH_API_KEY")
        self.mock_mode = self.api_key is None
        self.session = requests.Session() if _HAS_REQUESTS else None

        if self.mock_mode:
            warnings.warn(
                "⚠️  No SYNTH_API_KEY found. Running in MOCK mode — "
                "responses will be loaded from local mock_data/ files. "
                "Set SYNTH_API_KEY environment variable to use the real API.",
                stacklevel=2,
            )

        # Resolve mock data directory
        if mock_data_dir:
            self._mock_dir = Path(mock_data_dir)
        else:
            # Walk up from this file to find the repo root (where mock_data/ lives)
            self._mock_dir = Path(__file__).parent.parent / "mock_data"

    # ─── Core request methods ────────────────────────────────────────

    def _request(self, path: str, params: dict | None = None) -> dict | list:
        """Make an authenticated GET request to the Synth API."""
        if not _HAS_REQUESTS:
            raise RuntimeError(
                "The 'requests' package is required for live API mode. "
                "Install with: pip install requests"
            )

        headers = {"Authorization": f"Apikey {self.api_key}"}
        resp = self.session.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()

    def _load_mock(self, *path_parts: str) -> dict | list:
        """Load a mock data JSON file."""
        filepath = self._mock_dir.joinpath(*path_parts)
        if not filepath.exists():
            raise FileNotFoundError(
                f"Mock data file not found: {filepath}\n"
                f"Run 'python scripts/generate_mock_data.py --api-key YOUR_KEY' "
                f"to generate mock data."
            )
        with open(filepath) as f:
            return json.load(f)

    def _get(self, path: str, mock_path_parts: list[str], params: dict | None = None) -> dict | list:
        """
        Unified getter: dispatches to real API or mock data based on mode.

        Args:
            path: API URL path (e.g. "/insights/volatility")
            mock_path_parts: Path components to the mock JSON file
                            (e.g. ["volatility", "BTC_24h.json"])
            params: Query parameters for the real API call
        """
        if self.mock_mode:
            return self._load_mock(*mock_path_parts)
        try:
            return self._request(path, params)
        except requests.exceptions.RequestException as e:
            warnings.warn(f"Live API failed for {path} with params {params}: {e}. Falling back to mock data.", stacklevel=2)
            try:
                return self._load_mock(*mock_path_parts)
            except Exception as mock_err:
                raise RuntimeError(f"Live API failed AND mock data unavailable: {e}") from mock_err

    # ─── Prediction Percentiles ──────────────────────────────────────

    def get_prediction_percentiles(self, asset: str, horizon: str = "24h") -> dict:
        """
        Get probabilistic price forecasts with percentile distributions.

        Args:
            asset: Asset symbol (BTC, ETH, SOL, XAU, SPY, NVDA, TSLA, AAPL, GOOGL)
            horizon: Forecast horizon — "1h" or "24h" (default: "24h")

        Returns:
            Dict with keys: current_price, forecast_future, forecast_past, realized
        """
        return self._get(
            "/insights/prediction-percentiles",
            ["prediction_percentiles", f"{asset}_{horizon}.json"],
            params={"asset": asset, "horizon": horizon},
        )

    # ─── Volatility ──────────────────────────────────────────────────

    def get_volatility(self, asset: str, horizon: str = "24h") -> dict:
        """
        Get forecasted and realized volatility metrics.

        Args:
            asset: Asset symbol
            horizon: "1h" or "24h" (default: "24h")

        Returns:
            Dict with keys: current_price, forecast_future, forecast_past, realized
        """
        return self._get(
            "/insights/volatility",
            ["volatility", f"{asset}_{horizon}.json"],
            params={"asset": asset, "horizon": horizon},
        )

    # ─── Option Pricing ──────────────────────────────────────────────

    def get_option_pricing(self, asset: str) -> dict:
        """
        Get theoretical option prices derived from Synth's probability distributions.

        Args:
            asset: Asset symbol

        Returns:
            Dict with keys: current_price, expiry_time, call_options, put_options
        """
        return self._get(
            "/insights/option-pricing",
            ["option_pricing", f"{asset}.json"],
            params={"asset": asset},
        )

    # ─── Liquidation ─────────────────────────────────────────────────

    def get_liquidation(self, asset: str) -> dict:
        """
        Get liquidation probability estimates at various price change levels.

        Args:
            asset: Asset symbol

        Returns:
            Dict with keys: current_price, data (array of liquidation levels)
        """
        return self._get(
            "/insights/liquidation",
            ["liquidation", f"{asset}.json"],
            params={"asset": asset},
        )

    # ─── LP Bounds ───────────────────────────────────────────────────

    def get_lp_bounds(self, asset: str) -> dict:
        """
        Get optimal liquidity provider ranges with impermanent loss estimates.

        Args:
            asset: Asset symbol

        Returns:
            Dict with keys: current_price, data (array of interval analyses)
        """
        return self._get(
            "/insights/lp-bounds",
            ["lp_bounds", f"{asset}.json"],
            params={"asset": asset},
        )

    # ─── LP Probabilities ────────────────────────────────────────────

    def get_lp_probabilities(self, asset: str) -> dict:
        """
        Get price distribution probabilities for LP range decisions.

        Args:
            asset: Asset symbol

        Returns:
            Dict with keys: current_price, data
        """
        return self._get(
            "/insights/lp-probabilities",
            ["lp_probabilities", f"{asset}.json"],
            params={"asset": asset},
        )

    # ─── Polymarket ──────────────────────────────────────────────────

    def get_polymarket_daily(self, asset: str = "BTC") -> dict:
        """
        Get daily up/down comparison between Synth forecasts and Polymarket prices.

        Args:
            asset: Asset symbol (BTC, ETH, SOL). Default: BTC

        Returns:
            Dict with Synth vs Polymarket probability comparison
        """
        return self._get(
            "/insights/polymarket/up-down/daily",
            ["polymarket", f"up_down_daily_{asset}.json"],
            params={"asset": asset},
        )

    def get_polymarket_hourly(self, asset: str = "BTC") -> dict:
        """
        Get hourly up/down comparison between Synth forecasts and Polymarket prices.

        Args:
            asset: Asset symbol (BTC, ETH, SOL). Default: BTC

        Returns:
            Dict with Synth vs Polymarket probability comparison
        """
        return self._get(
            "/insights/polymarket/up-down/hourly",
            ["polymarket", f"up_down_hourly_{asset}.json"],
            params={"asset": asset},
        )

    def get_polymarket_15min(self, asset: str = "BTC") -> dict:
        """
        Get 15-minute up/down comparison between Synth forecasts and Polymarket prices.

        Args:
            asset: Asset symbol (BTC, ETH, SOL). Default: BTC

        Returns:
            Dict with Synth vs Polymarket probability comparison
        """
        return self._get(
            "/insights/polymarket/up-down/15min",
            ["polymarket", f"up_down_15min_{asset}.json"],
            params={"asset": asset},
        )

    def get_polymarket_5min(self, asset: str = "BTC") -> dict:
        """
        Get 5-minute up/down comparison between Synth forecasts and Polymarket prices.

        Args:
            asset: Asset symbol (BTC, ETH, SOL). Default: BTC

        Returns:
            Dict with Synth vs Polymarket probability comparison
        """
        return self._get(
            "/insights/polymarket/up-down/5min",
            ["polymarket", f"up_down_5min_{asset}.json"],
            params={"asset": asset},
        )

    def get_polymarket_range(self) -> list:
        """
        Get Polymarket range comparison with Synth probabilities.

        Returns:
            List of price range comparisons between Synth and Polymarket
        """
        return self._get(
            "/insights/polymarket/range",
            ["polymarket", "range.json"],
        )

    # ─── Leaderboard ─────────────────────────────────────────────────

    def get_leaderboard(self, asset: str = "BTC", days: int = 14, limit: int = 10) -> list:
        """
        Get current miner rankings for an asset.

        Args:
            asset: Asset symbol (default: "BTC")
            days: Number of days to aggregate (default: 14)
            limit: Number of top miners to return (default: 10)

        Returns:
            List of miner ranking dicts with keys: neuron_uid, rewards, updated_at, etc.
        """
        return self._get(
            "/v2/leaderboard/latest",
            ["leaderboard", f"latest_{asset}.json"],
            params={"asset": asset, "days": days, "limit": limit},
        )
