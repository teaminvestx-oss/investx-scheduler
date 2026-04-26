import os
import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

EXCEL_PATH = os.environ.get("EXCEL_PATH", "InvestX_trading_template_filled.xlsx")
ACTIVE_STATUSES = {"Activa", "active", "Covered"}
TICKER_COLUMNS = ["ticker", "Ticker", "TICKER", "symbol", "Symbol"]


def _load_active_df() -> tuple[pd.DataFrame, str | None]:
    df = pd.read_excel(EXCEL_PATH, header=2)
    df.columns = [str(c).strip() for c in df.columns]

    if "coverage_status" in df.columns:
        df = df[df["coverage_status"].isin(ACTIVE_STATUSES)]

    today = date.today()
    if "stale_after" in df.columns:
        df["stale_after"] = pd.to_datetime(df["stale_after"], errors="coerce").dt.date
        df = df[df["stale_after"] >= today]

    ticker_col = next((c for c in TICKER_COLUMNS if c in df.columns), None)
    return df, ticker_col


def get_ticker_data(ticker: str) -> dict | None:
    try:
        df, ticker_col = _load_active_df()
        if ticker_col is None:
            logger.error("No ticker column found in Excel")
            return None

        match = df[df[ticker_col].astype(str).str.upper() == ticker.upper()]
        if match.empty:
            return None

        return match.iloc[0].to_dict()
    except FileNotFoundError:
        logger.error(f"Excel file not found: {EXCEL_PATH}")
        return None
    except Exception as e:
        logger.error(f"Error reading Excel for ticker {ticker}: {e}")
        return None


def get_all_active_tickers() -> list[str]:
    try:
        df, ticker_col = _load_active_df()
        if ticker_col is None:
            return []

        return sorted(df[ticker_col].dropna().astype(str).str.upper().tolist())
    except FileNotFoundError:
        logger.error(f"Excel file not found: {EXCEL_PATH}")
        return []
    except Exception as e:
        logger.error(f"Error reading Excel for portfolio: {e}")
        return []
