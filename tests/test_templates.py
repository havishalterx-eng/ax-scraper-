"""Tests that template prompts and direct plans stay in sync.

A template is two descriptions of the same job: the prompt the model reads,
and the fixed plan that runs without a model. If one changes and the other
does not, the template silently does the wrong thing. These tests catch that.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from browser_mcp import templates
from browser_mcp.server import mcp

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_URL_RE = re.compile(r"https?://\S+")


def _tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


TOOL_NAMES = _tool_names()


def _placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _urls(text: str) -> set[str]:
    return set(_URL_RE.findall(text))


def _input_keys(template: dict) -> set[str]:
    """Every declared input plus the derived `<key>_plus` form the renderer creates."""
    keys: set[str] = set()
    for spec in template["inputs"]:
        keys.add(spec["key"])
        keys.add(f"{spec['key']}_plus")
    return keys


@pytest.mark.parametrize("template", templates.TEMPLATES, ids=lambda t: t["id"])
def test_prompt_renders(template: dict) -> None:
    rendered = templates.render_prompt(template["id"], {})
    assert rendered, f"template {template['id']} rendered an empty prompt"
    assert "{" not in rendered, f"unfilled placeholder in {template['id']}: {rendered}"


@pytest.mark.parametrize(
    "template",
    [t for t in templates.TEMPLATES if t.get("direct")],
    ids=lambda t: t["id"],
)
def test_plan_renders_with_real_tools(template: dict) -> None:
    plan = templates.render_plan(template["id"], {})
    assert plan is not None, f"template {template['id']} has a direct list but render_plan returned None"
    for step in plan:
        assert step["tool"] in TOOL_NAMES, f"unknown tool {step['tool']} in {template['id']}"


@pytest.mark.parametrize(
    "template",
    [t for t in templates.TEMPLATES if t.get("direct")],
    ids=lambda t: t["id"],
)
def test_prompt_and_plan_urls_agree(template: dict) -> None:
    prompt = templates.render_prompt(template["id"], {})
    plan = templates.render_plan(template["id"], {})
    assert prompt and plan

    prompt_urls = _urls(prompt)
    plan_urls: set[str] = set()
    for step in plan:
        for value in step["args"].values():
            if isinstance(value, str):
                plan_urls.update(_urls(value))

    assert plan_urls == prompt_urls, (
        f"URL mismatch in {template['id']}: prompt has {prompt_urls}, plan has {plan_urls}"
    )


@pytest.mark.parametrize(
    "template",
    [
        t
        for t in templates.TEMPLATES
        if t.get("direct") and any(i["key"] == "count" for i in t["inputs"])
    ],
    ids=lambda t: t["id"],
)
def test_count_matches_between_prompt_and_plan(template: dict) -> None:
    prompt = templates.render_prompt(template["id"], {})
    plan = templates.render_plan(template["id"], {})
    assert prompt and plan

    match = re.search(r"limit\s+(\d+)", prompt)
    assert match, f"prompt for {template['id']} has no limit number"
    prompt_count = int(match.group(1))

    plan_count = None
    for step in plan:
        if "limit" in step["args"]:
            plan_count = step["args"]["limit"]
    assert plan_count is not None, f"plan for {template['id']} has no limit"
    assert plan_count == prompt_count, (
        f"count mismatch in {template['id']}: prompt {prompt_count}, plan {plan_count}"
    )


@pytest.mark.parametrize(
    "template",
    [t for t in templates.TEMPLATES if t.get("direct")],
    ids=lambda t: t["id"],
)
def test_limit_is_int(template: dict) -> None:
    plan = templates.render_plan(template["id"], {})
    assert plan is not None
    for step in plan:
        if "limit" in step["args"]:
            assert isinstance(
                step["args"]["limit"], int
            ), f"limit is not int in {template['id']}: {step['args']['limit']!r}"


@pytest.mark.parametrize("template", templates.TEMPLATES, ids=lambda t: t["id"])
def test_inputs_cover_all_placeholders(template: dict) -> None:
    keys = _input_keys(template)

    prompt = templates.render_prompt(template["id"], {})
    if prompt:
        missing = _placeholders(prompt) - keys
        assert not missing, f"{template['id']} prompt uses undeclared placeholders: {missing}"

    plan = templates.render_plan(template["id"], {})
    if plan:
        for step in plan:
            for value in step["args"].values():
                if isinstance(value, str):
                    missing = _placeholders(value) - keys
                    assert not missing, (
                        f"{template['id']} plan uses undeclared placeholders: {missing}"
                    )


def test_google_results_has_no_plan() -> None:
    """Google search can return a consent wall or CAPTCHA that needs a human, so it
    deliberately stays on the model path. Adding a fixed plan here would be a bug.
    """
    assert templates.render_plan("google-results", {}) is None
