"""Tests for the direct-plan runner in api.py.

These tests do not open a browser or call Bedrock; they patch the model path
and exercise the handover logic directly.
"""

from __future__ import annotations

import asyncio

import pytest

from browser_mcp import api


def test_empty_plan_with_pending_message_promotes(monkeypatch):
    """A pending message on an empty plan must not raise NameError.

    The post-loop handover has no step to name, so `_handover` must accept
    `step=None` and the pending message must stay in the queue for the model
    path to drain.
    """
    promoted = []

    async def fake_agent(job_id, session, **kwargs):
        promoted.append(job_id)

    monkeypatch.setattr(api, "_run_agent_job", fake_agent)

    job_id, job = api._new_job("empty-plan-session", "test task")
    job["pending_messages"].append("wait, also include phone numbers")

    asyncio.run(api._run_direct_plan(job_id, "empty-plan-session", []))

    assert job["mode"] == "promoted"
    assert len(promoted) == 1
    assert job["pending_messages"] == ["wait, also include phone numbers"]
