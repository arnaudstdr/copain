"""Tests de `extract_finance_config` (lecture de la section `finances` du YAML)."""

from __future__ import annotations

import pytest

from bot.finance.config import (
    FinanceConfig,
    FinanceConfigError,
    extract_finance_config,
)


def test_empty_profile_returns_empty_config() -> None:
    cfg = extract_finance_config({})
    assert cfg == FinanceConfig.empty()
    assert not cfg.is_configured


def test_full_section_parses_all_items() -> None:
    raw = {
        "finances": {
            "currency": "EUR",
            "recurring": [
                {
                    "key": "loyer",
                    "label": "Loyer appartement",
                    "amount": 800,
                    "day": 5,
                    "kind": "expense",
                    "category": "logement",
                },
                {
                    "key": "netflix",
                    "label": "Netflix",
                    "amount": 17.99,
                    "day": 12,
                    "kind": "expense",
                },
                {
                    "key": "pel",
                    "label": "Versement PEL",
                    "amount": 200,
                    "day": 5,
                    "kind": "saving",
                },
            ],
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.currency == "EUR"
    assert len(cfg.recurring) == 3
    by_key = {it.key: it for it in cfg.recurring}
    assert by_key["loyer"].amount_cents == 80000
    assert by_key["loyer"].category == "logement"
    assert by_key["netflix"].amount_cents == 1799
    assert by_key["netflix"].category is None
    assert by_key["pel"].kind == "saving"


def test_missing_section_returns_empty() -> None:
    cfg = extract_finance_config({"identity": {"name": "Arnaud"}})
    assert cfg == FinanceConfig.empty()


def test_section_not_dict_returns_empty() -> None:
    cfg = extract_finance_config({"finances": "wrong"})
    assert cfg == FinanceConfig.empty()


def test_recurring_not_list_returns_empty_recurring() -> None:
    cfg = extract_finance_config({"finances": {"recurring": "wrong"}})
    assert cfg.recurring == ()


def test_invalid_kind_item_is_skipped() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "ok", "label": "OK", "amount": 10, "day": 1, "kind": "expense"},
                {"key": "ko", "label": "KO", "amount": 10, "day": 1, "kind": "banana"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert [it.key for it in cfg.recurring] == ["ok"]


def test_invalid_day_item_is_skipped() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "a", "label": "A", "amount": 10, "day": 0, "kind": "expense"},
                {"key": "b", "label": "B", "amount": 10, "day": 32, "kind": "expense"},
                {"key": "c", "label": "C", "amount": 10, "day": 5, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert [it.key for it in cfg.recurring] == ["c"]


def test_invalid_amount_item_is_skipped() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "a", "label": "A", "amount": -10, "day": 5, "kind": "expense"},
                {"key": "b", "label": "B", "amount": "huit", "day": 5, "kind": "expense"},
                {"key": "c", "label": "C", "amount": 0, "day": 5, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.recurring == ()


def test_missing_key_or_label_skipped() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "", "label": "A", "amount": 10, "day": 5, "kind": "expense"},
                {"key": "b", "label": "", "amount": 10, "day": 5, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.recurring == ()


def test_amount_float_converted_to_cents() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "n", "label": "Netflix", "amount": 17.99, "day": 12, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.recurring[0].amount_cents == 1799


def test_duplicate_keys_raise() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "loyer", "label": "A", "amount": 10, "day": 5, "kind": "expense"},
                {"key": "loyer", "label": "B", "amount": 20, "day": 5, "kind": "expense"},
            ]
        }
    }
    with pytest.raises(FinanceConfigError, match="loyer"):
        extract_finance_config(raw)


def test_default_currency_when_missing() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "a", "label": "A", "amount": 10, "day": 5, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.currency == "EUR"


def test_envelopes_parsed() -> None:
    raw = {
        "finances": {
            "envelopes": [
                {"category": "essence", "label": "Essence", "amount": 200},
                {"category": "courses", "label": "Courses", "amount": 600},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert len(cfg.envelopes) == 2
    assert cfg.envelopes[0].category == "essence"
    assert cfg.envelopes[0].amount_cents == 20000
    assert cfg.envelopes[1].amount_cents == 60000


def test_envelope_without_label_uses_category() -> None:
    raw = {"finances": {"envelopes": [{"category": "essence", "amount": 200}]}}
    cfg = extract_finance_config(raw)
    assert cfg.envelopes[0].label == "essence"


def test_envelope_invalid_amount_skipped() -> None:
    raw = {
        "finances": {
            "envelopes": [
                {"category": "ok", "amount": 100},
                {"category": "bad", "amount": -10},
                {"category": "no_amount"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert [e.category for e in cfg.envelopes] == ["ok"]


def test_envelope_missing_category_skipped() -> None:
    raw = {"finances": {"envelopes": [{"amount": 100}]}}
    cfg = extract_finance_config(raw)
    assert cfg.envelopes == ()


def test_envelope_duplicate_categories_raise() -> None:
    raw = {
        "finances": {
            "envelopes": [
                {"category": "essence", "amount": 100},
                {"category": "Essence", "amount": 200},
            ]
        }
    }
    with pytest.raises(FinanceConfigError, match=r"(?i)essence"):
        extract_finance_config(raw)


def test_find_envelope_is_case_insensitive() -> None:
    raw = {"finances": {"envelopes": [{"category": "Essence", "amount": 200}]}}
    cfg = extract_finance_config(raw)
    assert cfg.find_envelope("essence") is not None
    assert cfg.find_envelope("ESSENCE") is not None
    assert cfg.find_envelope("courses") is None


def test_is_configured_true_with_envelopes_only() -> None:
    raw = {"finances": {"envelopes": [{"category": "essence", "amount": 200}]}}
    cfg = extract_finance_config(raw)
    assert cfg.is_configured is True


def test_find_returns_item_or_none() -> None:
    raw = {
        "finances": {
            "recurring": [
                {"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"},
            ]
        }
    }
    cfg = extract_finance_config(raw)
    assert cfg.find("loyer") is not None
    assert cfg.find("inconnu") is None
