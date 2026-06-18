> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](../LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Session — How EIDOS browses as YOU

> EIDOS does not have its own accounts. It inherits **your** browser session —
> cookies, logins, preferences — from your Chromium profile. When it needs to
> log into a site or read your Gmail, it opens a Chromium that **is you**,
> without asking for your passwords. This is the mechanism behind autonomous
> registration, research on authenticated sites, and the Bridge's `/browse`.

---

## 1. The WebAgent (`core/eidos_web_agent.py`)

The class `WebAgent` does one thing: launch **your** Chromium with **your**
profile, connected via Chrome DevTools Protocol (CDP), so EIDOS navigates the
web as you. Here is exactly how:

```
SER's Chromium profile                EIDOS's temporary copy
~/.config/chromium/                   /tmp/eidos_chrome_profile/
├── Local State  ─────copy────→       ├── Local State
└── Default/                          └── Default/
    ├── Cookies ─────────copy────→        ├── Cookies
    ├── Cookies-journal ───copy────→      ├── Cookies-journal
    ├── Login Data ───────copy────→       ├── Login Data
    ├── Preferences ───────copy────→      ├── Preferences
    ├── Secure Preferences ─copy────→     ├── Secure Preferences
    ├── Network/ ─────────copy────→       ├── Network/
    ├── Local Storage/ ────copy────→      ├── Local Storage/
    └── Session Storage/ ──copy────→      └── Session Storage/
```

### Why a copy?

If EIDOS opened your real Chromium profile directly, it would **lock** it — you
couldn't use your browser while EIDOS works. The copy avoids the lock while
keeping the session alive. The key file is `Local State` (root level): it
contains the **encryption key** for the cookies. Without it, cookies are
unreadable. Both are copied together.

### The CDP connection

```python
# Simplified from core/eidos_web_agent.py
chromium --remote-debugging-port=9333 \
         --user-data-dir=/tmp/eidos_chrome_profile \
         --no-first-run --disable-session-crashed-bubble

# Then connect:
pw.chromium.connect_over_cdp("http://localhost:9333")
# → Now the Playwright browser IS your Chromium with your cookies
```

---

## 2. What this enables

| Capability | How it works |
|:-----------|:-------------|
| **Login without password** | Sites where you're already logged in (Gmail, GitHub, labex.io, forums) recognize the cookies. EIDOS clicks "Continue with Google" and **is already you**. |
| **Read your Gmail** | `gmail_search("verification")` → `gmail_open_first()` → `gmail_get_body()`. EIDOS reads verification emails from platforms it registered on. |
| **Browse authenticated sites** | Any site you're logged into — EIDOS navigates as you, reads docs, fills forms. |
| **Research behind logins** | The Bridge endpoint `/browse` opens the page with your session, so the borrowed AI sees what you see. |
| **Registration verification** | EIDOS registers on a platform → the verification email arrives in your Gmail → EIDOS opens it and clicks the link. All with your session. |

---

## 3. The Gmail helpers

```python
agent = WebAgent()                # uses ~/.config/chromium
agent.connect(headless=False)     # False = visible (SER sees it)
agent.goto("https://mail.google.com")

# Find and open a verification email
agent.gmail_search("verify your email")
agent.gmail_open_first()          # clicks the first result
body = agent.gmail_get_body()     # reads the email content

agent.close()
```

> This is used by `core/eidos_register.py` to complete autonomous registration.
> If the verification link is in the email body, EIDOS extracts it and opens it.

---

## 4. Creating a Colony character with your session

A Colony character can be born from a `claw` or `api_llm` connection that
uses the WebAgent behind the scenes. The character's learning loop absorbs
knowledge from authenticated sites because the browser **is** SER:

```python
from core.character_lifecycle import get_lifecycle

lc = get_lifecycle()
lc.birth_from_connection(
    name="ser_explorer",
    connection_type="claw",        # "claw" = browser-based exploration
    connection_data={
        "browser_profile": "~/.config/chromium",   # your cookies
        "start_url": "https://labex.io/learn/",    # authenticated course
    },
    emoji="🧬",
    target_nodes=100,
)
# → colony_ser_explorer is born, absorbs knowledge from labex
# → authenticated because it uses SER's Chromium session
```

---

## 5. Security

- **It is your session.** A borrowed AI using the Bridge sees the same pages
  you see. Never point the Bridge at a public port without understanding this.
- **The copy is temporary.** `/tmp/eidos_chrome_profile` is destroyed when the
  WebAgent closes. It is recreated fresh each time from your real profile.
- **Cookies stay local.** EIDOS never uploads, exfiltrates, or stores cookies
  outside your machine (constitution prohibition).
- **No password access.** EIDOS never reads your password. It uses the already
  authenticated session. If a site requires re-login (session expired), EIDOS
  asks for help.
- **Headless vs. visible.** `headless=False` means you see the browser window
  and can intervene. `headless=True` runs invisibly — use only for read-only
  research, never for registration.
