# tests/test_fmp.py
"""Tests for fmp.py — focused on error handling and robustness."""
import json
import pytest
from unittest.mock import patch, MagicMock


def _mock_response(text: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.side_effect = lambda: json.loads(text)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} Client Error", response=resp
        )
    return resp


class TestParsJsonLenient:
    def test_clean_array(self):
        from scheduler.tools.fmp import _parse_json_lenient
        assert _parse_json_lenient('[{"a":1}]') == [{"a": 1}]

    def test_clean_empty_array(self):
        from scheduler.tools.fmp import _parse_json_lenient
        assert _parse_json_lenient("[]") == []

    def test_extra_data_after_array(self):
        """FMP sometimes returns extra content after the JSON — must not crash."""
        from scheduler.tools.fmp import _parse_json_lenient
        text = '[{"symbol":"AAPL"}]\n{"status":"ok"}'
        result = _parse_json_lenient(text)
        assert result == [{"symbol": "AAPL"}]

    def test_extra_newline_after_array(self):
        from scheduler.tools.fmp import _parse_json_lenient
        assert _parse_json_lenient('[{"a":1}]\n\n') == [{"a": 1}]

    def test_truly_invalid_json_still_raises(self):
        from scheduler.tools.fmp import _parse_json_lenient
        with pytest.raises(json.JSONDecodeError):
            _parse_json_lenient("not json at all {{")


class TestFmpScreenerRobustness:
    """fmp_screener must not crash when API returns extra data or PEAD fetch fails."""

    def _make_screener_response(self, data: list | str):
        if isinstance(data, list):
            text = json.dumps(data)
        else:
            text = data
        return _mock_response(text)

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_handles_extra_data(self, mock_get, mock_pead, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        # Screener returns array followed by extra JSON blob
        extra_data_response = '[{"symbol":"AAPL","price":150}]\n{"meta":"ok"}'
        mock_get.return_value = _mock_response(extra_data_response)
        mock_pead.return_value = []

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")

        assert isinstance(result, list)
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["pead_candidate"] is False

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_pead_failure_is_nonfatal(self, mock_get, mock_pead, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        mock_get.return_value = _mock_response('[{"symbol":"NVDA","price":200}]')
        mock_pead.side_effect = Exception("429 Too Many Requests")

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")

        # Must return screener results even when PEAD fails
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["symbol"] == "NVDA"

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_empty_response_returns_empty_list(self, mock_get, mock_pead, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        mock_get.return_value = _mock_response("[]")
        mock_pead.return_value = []

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")
        assert result == []

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_non_list_response_returns_empty_list(self, mock_get, mock_pead, monkeypatch):
        """If FMP returns a dict instead of array (e.g. error object), return empty list."""
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        mock_get.return_value = _mock_response('{"error":"invalid parameters"}')
        mock_pead.return_value = []

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")
        assert result == []

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_pead_results_appended(self, mock_get, mock_pead, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        mock_get.return_value = _mock_response('[{"symbol":"NVDA","price":200}]')
        mock_pead.return_value = [{
            "symbol": "AAPL", "price": 150, "pead_candidate": True,
            "eps_surprise_pct": 25.0, "earnings_date": "2026-04-15",
        }]

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")

        symbols = [r["symbol"] for r in result]
        assert "NVDA" in symbols
        assert "AAPL" in symbols
        pead = next(r for r in result if r["symbol"] == "AAPL")
        assert pead["pead_candidate"] is True
        assert pead["eps_surprise_pct"] == 25.0

    @patch("scheduler.tools.fmp._fetch_pead_candidates")
    @patch("requests.get")
    def test_screener_deduplicates_pead_from_screener(self, mock_get, mock_pead, monkeypatch):
        """A stock appearing in both screener and PEAD should appear once, as PEAD."""
        monkeypatch.setenv("FMP_API_KEY", "test_key")
        mock_get.return_value = _mock_response('[{"symbol":"AAPL","price":150}]')
        mock_pead.return_value = [{
            "symbol": "AAPL", "price": 150, "pead_candidate": True,
            "eps_surprise_pct": 30.0, "earnings_date": "2026-04-16",
        }]

        from scheduler.tools.fmp import fmp_screener
        result = fmp_screener(api_key="test_key")

        # AAPL should appear once (as PEAD, not as screener result)
        aapl_records = [r for r in result if r["symbol"] == "AAPL"]
        assert len(aapl_records) == 1
        assert aapl_records[0]["pead_candidate"] is True
