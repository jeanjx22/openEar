"""Info services for weather, stocks, and news lookups.

Each service is wrapped in try/except with graceful degradation:
- Weather (Open-Meteo): free, no API key, reliable
- Stocks (yfinance): unofficial Yahoo Finance scraper, may break
- News (DuckDuckGo): unofficial scraper, may be rate-limited

All failures return user-friendly error messages rather than raising.

C7 fix: yfinance calls are wrapped in asyncio.to_thread() because
yfinance uses synchronous HTTP (urllib3/requests) internally.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


async def get_weather(
    latitude: float = 37.39,
    longitude: float = -122.08,
    city_name: str = "San Jose",
) -> str:
    """Get current weather and forecast from Open-Meteo.

    Returns a formatted string or an error message.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "America/Los_Angeles",
                    "forecast_days": 3,
                },
            )
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            temp = current.get("temperature_2m", "N/A")
            humidity = current.get("relative_humidity_2m", "N/A")
            wind = current.get("wind_speed_10m", "N/A")

            lines = [
                f"Weather in {city_name}:",
                f"Now: {temp}F, Humidity: {humidity}%, Wind: {wind} mph",
                "",
                "Forecast:",
            ]

            dates = daily.get("time", [])
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            rain = daily.get("precipitation_probability_max", [])

            for i in range(min(3, len(dates))):
                lines.append(
                    f"  {dates[i]}: {lows[i]}F - {highs[i]}F, "
                    f"Rain: {rain[i]}%"
                )

            return "\n".join(lines)

    except Exception as e:
        logger.error("Weather lookup failed: %s", e)
        return "Weather data is temporarily unavailable. Please try again later."


def _get_stock_quote_sync(symbol: str) -> str:
    """Synchronous stock quote fetch (runs in asyncio.to_thread).

    C7: yfinance uses urllib3/requests internally and blocks. This
    function is called via asyncio.to_thread() from the async wrapper.
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol.upper())
    info = ticker.info

    if not info or "regularMarketPrice" not in info:
        # Try fast_info as fallback
        fast = ticker.fast_info
        price = getattr(fast, "last_price", None)
        prev_close = getattr(fast, "previous_close", None)
        if price is None:
            return f"No data available for {symbol.upper()}."
        change = (
            f" ({((price - prev_close) / prev_close * 100):+.2f}%)"
            if prev_close
            else ""
        )
        return f"{symbol.upper()}: ${price:.2f}{change}"

    price = info.get("regularMarketPrice", "N/A")
    prev_close = info.get("regularMarketPreviousClose", 0)
    name = info.get("shortName", symbol.upper())
    market_cap = info.get("marketCap", 0)

    change_pct = ""
    if prev_close and price != "N/A":
        change_pct = f" ({((price - prev_close) / prev_close * 100):+.2f}%)"

    cap_str = ""
    if market_cap:
        if market_cap >= 1e12:
            cap_str = f"${market_cap / 1e12:.1f}T"
        elif market_cap >= 1e9:
            cap_str = f"${market_cap / 1e9:.1f}B"
        else:
            cap_str = f"${market_cap / 1e6:.0f}M"

    return (
        f"{name} ({symbol.upper()})\n"
        f"Price: ${price:.2f}{change_pct}\n"
        f"Market Cap: {cap_str}"
    )


async def get_stock_quote(symbol: str) -> str:
    """Get stock quote from yfinance.

    Wrapped with graceful degradation. Returns a formatted string
    or an error message if Yahoo Finance is unavailable.

    C7: The actual yfinance calls run in asyncio.to_thread() to avoid
    blocking the event loop.
    """
    try:
        return await asyncio.to_thread(_get_stock_quote_sync, symbol)
    except Exception as e:
        logger.error("Stock lookup failed for %s: %s", symbol, e)
        return (
            f"Stock data is temporarily unavailable for {symbol.upper()} -- "
            "Yahoo Finance may be experiencing issues. Try again later."
        )


async def search_news(query: str, max_results: int = 5) -> str:
    """Search news via DuckDuckGo.

    Returns formatted results or an error message.
    """
    try:
        from duckduckgo_search import AsyncDDGS

        async with AsyncDDGS() as ddgs:
            results = []
            async for r in ddgs.anews(query, max_results=max_results):
                results.append(r)

            if not results:
                return f"No news found for '{query}'."

            lines = [f"News for '{query}':"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                source = r.get("source", "")
                date = r.get("date", "")
                body = r.get("body", "")[:100]
                lines.append(f"\n{i}. {title}")
                if source:
                    lines.append(f"   Source: {source}")
                if date:
                    lines.append(f"   Date: {date}")
                if body:
                    lines.append(f"   {body}...")

            return "\n".join(lines)

    except Exception as e:
        logger.error("News lookup failed: %s", e)
        return "News lookup is temporarily unavailable. Please try again later."
