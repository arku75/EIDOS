# Autonomous Registration — How EIDOS signs up on platforms

> EIDOS can register itself on websites using its own email
> (`EIDOS_LOGIN_EMAIL` / `EIDOS_PASSWORD` from `~/.eidos/secrets.env`). It
> opens a real browser, fills the form, reads the verification email in Gmail,
> and clicks the confirmation link — autonomously. If it hits a captcha, it
> **stops and asks SER** (interactive mode). It never tries to bypass captchas.

---

## 1. The module: `core/eidos_register.py`

Two main functions:

| Function | Mode | Description |
|:---------|:-----|:------------|
| `register(platform)` | **Headless** | Fully autonomous: opens headless Chromium with SER's cookies, fills email/password, submits, reads Gmail for the verification link, clicks it. If anything fails → reports to SER's inbox. |
| `register_interactive(platform, wait_secs=300)` | **Visible** | Opens a REAL VISIBLE Chromium, pre-fills the email, and WAITS for SER to solve the captcha / complete the form. Detects successful login and reports. |

---

## 2. How it works (headless path)

```
1. receive directive: "regístrate en labex.io"
           ↓
2. open headless Chromium with SER's cookies (WebAgent, see SESSION.md)
           ↓
3. detect page type: login? register? OAuth?
           ↓
4a. Google OAuth detected → click "Continue with Google"
    → already authenticated (SER's cookies) → logged in ✅
           ↓
4b. Email/password form → fill EIDOS_LOGIN_EMAIL + EIDOS_PASSWORD → submit
           ↓
5. captcha appeared? → STOP. Report to SER: "necesito ayuda, hay captcha"
           ↓
6. no captcha → check for "verify your email" message
           ↓
7. open Gmail (authenticated via SER's Chromium session)
    → gmail_search("verify") → gmail_open_first() → gmail_get_body()
    → extract verification link → open it
           ↓
8. detected logged-in state? → SUCCESS. Persist site_access to graph.
    Could not complete? → Report to SER's inbox (action_needed).
```

---

## 3. Interactive mode (captcha / complex forms)

```bash
python3 -m core.eidos_register labex.io --interactive
```

What happens:
1. Opens Chromium **VISIBLE** with SER's session.
2. Navigates to the registration/login page.
3. Pre-fills the email field (if found).
4. **Waits** (polling every few seconds, up to `wait_secs`) for SER to
   complete the form / solve the captcha / click through.
5. Detects successful login → reports and persists.
6. The browser window stays open — SER closes it when done.

> This is the **legitimate path** for sites with captcha/TLS fingerprinting.
> EIDOS never tries to evade anti-bot detection. The rule: if a site blocks
> headless, use visible mode with SER present. (S125-F, S125-J)

---

## 4. Per-site profiles

EIDOS remembers how to register on a site:

```python
# After first successful login, EIDOS stores:
site_access = {
    "platform": "labex.io",
    "method": "google_oauth",       # or "email_password"
    "last_login": 1687020000,
    "cookies_valid": True,
}
```

Next time, it uses the same method. This is built on the **skill
generalization** mechanism (`core/eidos_skills.py`): the concept "register on a
website" is one skill; each successful site reinforces it.

---

## 5. Routing in study_queue

When you add a study directive containing "regístrate", it routes to the
registration path **before** the web/concept paths:

```python
# study_queue.py
if _is_register(directive):
    r = _do_register(directive)     # → eidos_register
elif _is_web(directive):
    # browse / research
else:
    # concept study
```

---

## 6. Limits & honesty

- **No captcha bypass.** If the page has a captcha, EIDOS reports
  "action_needed" to SER's inbox and stops. It does not attempt to solve it.
- **No blind mass registration.** The constitution forbids autonomous
  registration without SER's awareness. Each registration is one site, one
  directive.
- **Rate limits.** Consecutive registrations are throttled.
- **labex.io confirmed anti-bot** (S125-E). For such sites, only interactive
  mode works — SER must be present.
