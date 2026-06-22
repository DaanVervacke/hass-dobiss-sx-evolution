"""Tests for repair issue creation/deletion in DobissController."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.dobiss_sx_evolution.const import DOMAIN
from custom_components.dobiss_sx_evolution.controller import (
    RECONNECT_BACKOFF_MAX_S,
    DobissController,
)


def _make_controller(hass: HomeAssistant, entry_id: str = "test_entry_id") -> DobissController:
    """Build a minimal DobissController with no outputs."""
    return DobissController(
        hass,
        host="192.168.1.10",
        port=29536,
        interface="can0",
        lights=[],
        dimmers=[],
        shutters=[],
        entry_id=entry_id,
    )


async def test_raise_repair_issue_creates_issue(hass: HomeAssistant) -> None:
    """_raise_repair_issue creates an issue in the registry."""
    ctrl = _make_controller(hass)
    assert not ctrl._repair_issue_active

    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ctrl._issue_id())
    assert issue is not None
    assert issue.translation_key == "cannot_connect"
    assert ctrl._repair_issue_active is True


async def test_raise_repair_issue_idempotent(hass: HomeAssistant) -> None:
    """Calling _raise_repair_issue twice does not duplicate the issue."""
    ctrl = _make_controller(hass)
    ctrl._raise_repair_issue()
    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ctrl._issue_id())
    assert issue is not None
    assert ctrl._repair_issue_active is True


async def test_clear_repair_issue_removes_issue(hass: HomeAssistant) -> None:
    """_clear_repair_issue removes the issue once the connection recovers."""
    ctrl = _make_controller(hass)
    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id()) is not None

    ctrl._clear_repair_issue()

    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id()) is None
    assert ctrl._repair_issue_active is False


async def test_clear_repair_issue_idempotent(hass: HomeAssistant) -> None:
    """Calling _clear_repair_issue when no issue is active is safe."""
    ctrl = _make_controller(hass)
    assert not ctrl._repair_issue_active
    # Should not raise
    ctrl._clear_repair_issue()
    assert ctrl._repair_issue_active is False


async def test_issue_id_is_entry_specific(hass: HomeAssistant) -> None:
    """Each entry gets its own issue ID so multiple entries don't collide."""
    ctrl_a = _make_controller(hass, entry_id="entry_aaa")
    ctrl_b = _make_controller(hass, entry_id="entry_bbb")
    assert ctrl_a._issue_id() != ctrl_b._issue_id()
    assert "entry_aaa" in ctrl_a._issue_id()
    assert "entry_bbb" in ctrl_b._issue_id()
