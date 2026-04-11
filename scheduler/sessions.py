import json
from typing import Optional


def build_pre_market_prompt(date: str, market_opens_in: str) -> str:
    return f"SESSION: pre_market | DATE: {date} | MARKET_OPENS_IN: {market_opens_in}"


def build_market_open_prompt(date: str, time_et: str) -> str:
    return f"SESSION: market_open | DATE: {date} | TIME: {time_et} ET"


def build_health_check_prompt(date: str, positions: list) -> str:
    positions_json = json.dumps(positions)
    return f"SESSION: health_check | DATE: {date} | TIME: 13:00 ET | POSITIONS: {positions_json}"


def build_eod_reflection_prompt(date: str, trades_today: list) -> str:
    trades_json = json.dumps(trades_today)
    return f"SESSION: eod_reflection | DATE: {date} | TIME: 15:45 ET | TRADES_TODAY: {trades_json}"


def build_weekly_review_prompt(date: str, week_number: int) -> str:
    return f"SESSION: weekly_review | DATE: {date} | WEEK: {week_number}"
