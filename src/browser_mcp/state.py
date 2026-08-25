from __future__ import annotations

from typing import Any

# Walks the visible DOM, tags every interactive element with a stable
# data-bmcp-idx attribute, and returns a compact description of each.
# Same shape as BrowserAct's `state` command and Microsoft's playwright-mcp
# accessibility snapshot: index in, index out, no selectors for the caller
# to write.
STATE_JS = r"""
() => {
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  };

  const interactiveSelector = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="checkbox"]',
    '[role="radio"]', '[role="tab"]', '[role="menuitem"]',
    '[onclick]', '[contenteditable="true"]', '[tabindex]'
  ].join(',');

  const seen = new Set();
  const nodes = Array.from(document.querySelectorAll(interactiveSelector)).filter((el) => {
    if (seen.has(el)) return false;
    seen.add(el);
    if (el.hasAttribute('tabindex') && el.getAttribute('tabindex') === '-1') return false;
    if (el.disabled) return false;
    return isVisible(el);
  });

  const items = nodes.map((el, i) => {
    const idx = i + 1;
    el.setAttribute('data-bmcp-idx', String(idx));
    const tag = el.tagName.toLowerCase();
    const rawText = el.innerText !== undefined ? el.innerText : (el.value || '');
    const text = String(rawText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    return {
      idx,
      tag,
      type: el.getAttribute('type') || '',
      text,
      placeholder: el.getAttribute('placeholder') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      href: el.getAttribute('href') || '',
    };
  });

  return { url: window.location.href, title: document.title, items };
}
"""


def format_state(data: dict[str, Any]) -> str:
    lines = [f"url={data['url']}", f"title={data['title']}", ""]
    items = data.get("items", [])
    for item in items:
        attrs = []
        if item["type"]:
            attrs.append(f'type={item["type"]}')
        if item["placeholder"]:
            attrs.append(f'placeholder="{item["placeholder"]}"')
        if item["ariaLabel"]:
            attrs.append(f'aria-label="{item["ariaLabel"]}"')
        if item["href"]:
            attrs.append(f'href="{item["href"]}"')
        attr_str = " " + " ".join(attrs) if attrs else ""
        line = f'[{item["idx"]}]<{item["tag"]}{attr_str}>'
        if item["text"]:
            line += f" {item['text']}"
        lines.append(line)
    if not items:
        lines.append("(no interactive elements found)")
    return "\n".join(lines)
