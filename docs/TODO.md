# TODO

Known issues that are not fixed yet, with where they live and a suggested fix.

## Live status panel is read out every second

- **Problem:** the status card on `/live` has `aria-live="polite"`, and the SSE
  stream swaps its contents every second (`render()` in `f1dash/web/live.py`).
  Screen readers keep reading the lap counter and session clock.
- **Where:** `f1dash/templates/live.html` (the `section.card.span-12` around
  `sse-swap="status"`), `f1dash/templates/partials/live_status.html`,
  `f1dash/static/js/app.js`.
- **Fix:** remove `aria-live` from that section. Add one
  `<p id="live-announcer" class="visually-hidden" aria-live="polite" aria-atomic="true">`
  (the `.visually-hidden` class already exists in `app.css`). In `app.js`,
  listen for `htmx:sseMessage`: `ev.target` is the element with `sse-swap`.
  Write to the announcer only when its text changes, for:
  - the track status (put a `data-announce` attribute on `.status-bar` with the
    track status, plus "Replay finished" when `final` is set);
  - the newest race-control message (the first `li` in `.rc-list`; messages are
    newest first);
  - stream notices, which have no `.status-bar`.

## Home page fails as a whole when one source is down

- **Problem:** `Dashboard.home()` in `f1dash/services.py` calls OpenF1 (latest
  race, podium, sessions) and Jolpica (next event, standings) in one go. Any
  `UpstreamError` reaches the app-wide handler in `f1dash/__init__.py`, which
  turns the whole page into a 503. Also, `Dashboard.next_event()` swallows
  *every* exception (`except Exception`), so when Jolpica is down the hero card
  wrongly says "No upcoming sessions published".
- **Where:** `Dashboard.home`, `Dashboard.next_event` (`f1dash/services.py`),
  `home()` in `f1dash/web/pages.py`, `f1dash/templates/home.html`.
- **Fix:**
  - Split `home()` into three sections (`next`: Jolpica, `latest`: OpenF1,
    `standings`: Jolpica). Build each one in its own `try/except UpstreamError`,
    log it, fall back to empty values and return a `failed` list.
  - In `next_event`, catch only `UpstreamError`. Re-raise it for the current
    year and skip it for next year, which may not be published yet.
  - Careful: `/live` also calls `next_event` (`f1dash/web/live.py`, `page()`).
    Wrap that call too, or a Jolpica outage will start breaking `/live`.
  - In `home.html`, show an inline `.notice` naming the failing source in place
    of each failed section.
  - Return 200 when at least one section rendered, and 503 only when all three
    failed.
  - Tests: subclass `FixtureFetcher` (in `tests/conftest.py`) so it raises
    `UpstreamError` for one host (`api.jolpi.ca` / `api.openf1.org`). Assert:
    - Jolpica down: 200, the podium is present and the standings show a notice.
    - OpenF1 down: 200, the standings are present and there is no `class="podium"`.
    - Both down: 503.

## Theme script breaks where storage is blocked

- **Problem:** `localStorage` throws (`SecurityError`) when site data is
  blocked, for example by some privacy settings or in sandboxed iframes. The
  inline script in `base.html` then fails, and the theme toggle fails on every
  click.
- **Where:** `f1dash/templates/base.html` (the inline "Apply the saved theme"
  script), `toggleTheme()` in `f1dash/static/js/app.js`.
- **Fix:** wrap `localStorage.getItem` and `localStorage.setItem` in
  `try { ... } catch (e) {}`. Set the theme on the page before the
  `setItem` call, so the toggle still works for the current page.

## CI hardening

- **Problem:** `.github/workflows/ci.yml` tests only Python 3.13 (while
  `requires-python = ">=3.12"`). It has no explicit `permissions`. The docker
  job builds the image but never runs it.
- **Fix:**
  - Add `permissions: contents: read` at the top level.
  - Add `strategy.matrix.python-version: ["3.12", "3.13"]` to the `test` job.
    pip caching is already on via `setup-python` `cache: pip`; give it
    `cache-dependency-path: pyproject.toml`.
  - In the `docker` job, after the build:
    1. `docker run -d --name f1dash -p 8000:8000 -e F1DASH_OFFLINE=1 f1dash:ci`
       (check the offline env var name in `f1dash/config.py`);
    2. poll `curl -fsS http://127.0.0.1:8000/healthz` for up to about 30 s.
       The endpoint exists in `create_app` and does not touch upstreams;
    3. on failure, print `docker logs f1dash` and exit 1.
