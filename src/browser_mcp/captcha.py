"""Paid CAPTCHA solving via 2Captcha.

`request_human_help` already covers a CAPTCHA the honest way - pause the run,
wait for a person. That only helps when a person is actually watching. This
adds a second option for the case where nobody is: pay a solving service a
fraction of a cent, get a real token back, hand it to the page.

This is deliberately the only paid, per-request step anywhere in this tool -
everything else costs Bedrock tokens or nothing. It stays off unless
TWOCAPTCHA_API_KEY is set, so a run that never hits a CAPTCHA never touches
it, and one that does falls back to request_human_help rather than failing
if the key is missing or the solve fails.

Detection covers three widget families: reCAPTCHA v2, hCaptcha, and
Cloudflare Turnstile. reCAPTCHA v3 and enterprise variants are not handled -
they score a session rather than present a checkbox, and there is nothing on
the page to detect or a token field to fill.
"""

from __future__ import annotations

import asyncio
import json

import httpx

SUBMIT_URL = "https://2captcha.com/in.php"
RESULT_URL = "https://2captcha.com/res.php"

METHOD_BY_TYPE = {
    "recaptcha2": "userrecaptcha",
    "hcaptcha": "hcaptcha",
    "turnstile": "turnstile",
}

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 120

# 2Captcha's published per-1000-solve pricing at the time this was written -
# not read from a live pricing endpoint, since they do not expose one, and it
# drifts. Good for an order-of-magnitude line in a log or a webhook payload,
# not as a source of truth for what your account was actually charged; check
# 2captcha.com's own pricing page for that.
APPROX_COST_USD = {
    "recaptcha2": 0.0010,
    "hcaptcha": 0.0015,
    "turnstile": 0.0015,
}


class CaptchaSolveError(Exception):
    pass


# Finds the first known widget on the page and returns its type and sitekey,
# or null if none is present. The class-based selectors cover the documented
# way each vendor asks a site to embed its widget; the iframe fallback covers
# a widget rendered purely through JS, where the sitekey is not in an
# attribute anywhere but is always present in the challenge iframe's own src.
DETECT_CAPTCHA_JS = """
() => {
  const attr = (sel, name) => {
    const el = document.querySelector(sel);
    return el ? el.getAttribute(name) : null;
  };

  let sitekey = attr('.g-recaptcha[data-sitekey]', 'data-sitekey');
  if (sitekey) {
    const el = document.querySelector('.g-recaptcha[data-sitekey]');
    return { type: 'recaptcha2', sitekey, invisible: el.getAttribute('data-size') === 'invisible' };
  }
  sitekey = attr('.h-captcha[data-sitekey]', 'data-sitekey');
  if (sitekey) return { type: 'hcaptcha', sitekey, invisible: false };
  sitekey = attr('.cf-turnstile[data-sitekey]', 'data-sitekey');
  if (sitekey) return { type: 'turnstile', sitekey, invisible: false };

  const iframe = document.querySelector(
    'iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"], ' +
    'iframe[src*="hcaptcha.com/captcha"], iframe[src*="challenges.cloudflare.com"]'
  );
  if (iframe) {
    const src = iframe.src;
    const m = src.match(/[?&]k=([^&]+)/) || src.match(/[?&]sitekey=([^&]+)/);
    const key = m ? decodeURIComponent(m[1]) : null;
    if (key) {
      if (src.includes('hcaptcha')) return { type: 'hcaptcha', sitekey: key, invisible: false };
      if (src.includes('cloudflare')) return { type: 'turnstile', sitekey: key, invisible: false };
      return { type: 'recaptcha2', sitekey: key, invisible: false };
    }
  }
  return null;
}
"""

# Writes a solved token into whichever hidden field the widget checks.
# Returns how many fields were touched - proof the write happened, not proof
# the site accepted the token. Many integrations validate on form submit by
# just reading this field, which is why the write alone is often enough;
# `build_callback_script` below covers the integrations that additionally
# require calling a registered callback.
INJECT_TOKEN_JS = """
({ token, kind }) => {
  const fieldSelectors = {
    recaptcha2: 'textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]',
    hcaptcha: 'textarea[name="h-captcha-response"], textarea#h-captcha-response',
    turnstile: 'input[name="cf-turnstile-response"], input[name="cf_challenge_response"]',
  };
  const sel = fieldSelectors[kind] || fieldSelectors.recaptcha2;
  const fields = Array.from(document.querySelectorAll(sel));
  fields.forEach((el) => {
    el.style.display = 'block';
    el.value = token;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  if (kind === 'hcaptcha') {
    // Some integrations check the reCAPTCHA-shaped field even for hCaptcha.
    document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach((el) => { el.value = token; });
  }
  return fields.length;
}
"""


def build_callback_script(token: str) -> str:
    """A <script> tag's worth of JS to fire a widget's data-callback.

    This cannot be done through `page.evaluate()`. patchright runs `evaluate`
    in an isolated JS world on purpose - the whole point of the fork is
    keeping automation invisible to a page that inspects its own `window` for
    CDP tells - and a `window` property is per-world, not shared, so evaluate
    can neither call a callback the page registered on its own global object
    nor read one back afterward to check. The DOM is shared regardless of
    world, so the field write above lands correctly and can be verified by
    reading the field; the callback cannot be verified the same way.

    Proven by writing the callback's result into the DOM instead of a window
    property: a synthetic hCaptcha container's data-callback, invoked via
    this function through `page.add_script_tag`, wrote its token into a DOM
    node exactly as the real widget's callback would. The same call made
    through `page.evaluate()` did not reach the callback at all.
    """
    token_json = json.dumps(token)
    return (
        "document.querySelectorAll("
        "'.g-recaptcha[data-callback], .h-captcha[data-callback], .cf-turnstile[data-callback]'"
        ").forEach(function (el) {"
        "  var name = el.getAttribute('data-callback');"
        f"  if (name && typeof window[name] === 'function') {{ try {{ window[name]({token_json}); }} catch (e) {{}} }}"
        "});"
    )


async def solve(api_key: str, captcha_type: str, sitekey: str, page_url: str) -> str:
    """Submits a widget to 2Captcha and polls for the solved token.

    Raises CaptchaSolveError on anything that isn't a clean solve - an
    unsupported type, a rejected submission, a solver-side error, or a
    timeout. The caller decides what to do next; this never falls back to
    request_human_help itself; a module doing paid work behind the caller's
    back is worse than one that just reports what happened.
    """
    method = METHOD_BY_TYPE.get(captcha_type)
    if method is None:
        raise CaptchaSolveError(f"unsupported captcha type: {captcha_type}")

    async with httpx.AsyncClient(timeout=15) as client:
        submit = await client.get(
            SUBMIT_URL,
            params={"key": api_key, "method": method, "sitekey": sitekey, "pageurl": page_url, "json": 1},
        )
        data = submit.json()
        if data.get("status") != 1:
            raise CaptchaSolveError(f"2Captcha rejected the job: {data.get('request')}")
        request_id = data["request"]

        elapsed = 0
        while elapsed < POLL_TIMEOUT_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
            poll = await client.get(
                RESULT_URL, params={"key": api_key, "action": "get", "id": request_id, "json": 1}
            )
            result = poll.json()
            if result.get("status") == 1:
                return result["request"]
            if result.get("request") != "CAPCHA_NOT_READY":
                raise CaptchaSolveError(f"2Captcha returned an error: {result.get('request')}")

        raise CaptchaSolveError(f"timed out after {POLL_TIMEOUT_S}s waiting for a solve")
