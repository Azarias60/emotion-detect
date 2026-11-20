"""Contracts owned by the top-level stateless capability components."""

import importlib.util

import pytest


def test_generic_analysis_result_is_not_a_public_contract():
    try:
        found = importlib.util.find_spec("server.analysis.contracts.types")
    except ModuleNotFoundError:
        found = None
    assert found is None
