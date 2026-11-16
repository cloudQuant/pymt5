# Demo acceptance and market-hours CI design

- Date: 2026-11-16
- Status: approved by the user on 2026-11-16, including investigation, test fixes, expanded demo API coverage, and a 100% coverage target

## Intent

Make the authorized, read-only MT5 demo acceptance suite useful during real market hours, keep ordinary CI and releases offline, and make client/server failures visible without claiming unsupported behavior works.

## Evidence and current behavior

- The unmodified offline suite passes: 1334 passed, 8 skipped; statement coverage is 98% (61 of 3870 statements uncovered).
- Live build 6320 checks passed for bootstrap, login/logout, symbols, heartbeat, health, account, account summary, terminal/version, positions/orders, trade history, full symbol details, symbol groups, and historical bars.
- EURUSD cmd=8 ticks were observed during active trading. A wire ask of 110289 with digits=5 corresponds to 1.10289 and agrees with nearby M1 bars.
- Two-symbol subscription and partial unsubscribe succeed. Removing the final symbol returns code=1 for both a four-byte zero count and an empty payload; ticks for the remaining symbol continue afterward. This is a server rejection, not a client success-code misclassification.
- The existing integration test fails only because its unconditional final unsubscribe assumes every server accepts an empty set. Current library behavior—raise `ProtocolError` and preserve the last confirmed set—is correct and must remain.

## Goals

1. Correct the integration contract without hiding server rejections or corrupting subscription state.
2. Add read-only live coverage for the successfully exercised APIs and real tick reception.
3. Make missing ticks a failure in explicitly scheduled CI rather than a green skip.
4. Add an opt-in, market-hours demo workflow at 01:00 and 13:00 UTC Monday–Friday plus manual dispatch.
5. Reach and enforce 100% offline statement coverage with meaningful deterministic tests.
6. Update English and Chinese README/development documentation and record sanitized acceptance evidence.

## Non-goals and anti-goals

- No trading, order modification/cancellation, account mutation, or demo-account creation.
- No credentials or private account/position values in source, logs, reports, or artifacts.
- No automatic disconnect/reconnect disguised as a successful final unsubscribe.
- No catching arbitrary errors, removing assertions, excluding production modules, or marking no-tick CI runs successful to obtain green results.
- No claim that build 6320 observations certify other brokers/builds or that offline coverage proves live compatibility.
- No live demo dependency in pull-request, ordinary CI, documentation deployment, or release jobs.

## Approaches

### Chosen: separate scheduled demo workflow plus honest compatibility contract

Keep default CI/release offline. Run `tests/test_integration.py` separately with protected demo credentials and `PYMT5_REQUIRE_TICK=1`. Split tick tests into real reception/partial unsubscribe and final-unsubscribe response/state contract. Accept either spec-compliant outcome for final unsubscribe: success clears local state; an exact code=1 rejection raises `ProtocolError` and preserves it. Connection close performs final cleanup.

### Rejected: live tests in the normal CI matrix

This would expose scheduled credentials to inappropriate triggers, make closed-market periods flaky, and couple releases to external market hours.

### Rejected: change or ignore cmd=7 response codes

Both candidate empty-set encodings were rejected and ticks continued. Treating code=1 as success or clearing local state would contradict observed server behavior.

## Implementation decisions

- `tests/test_integration.py` gains explicit credential/config validation, exact active-symbol selection, `PYMT5_REQUIRE_TICK=1` no-tick failure, read-only API tests, historical bars, full-symbol details, real tick reception, and subscription-state checks.
- Offline tests exercise integration helpers and both final-unsubscribe outcomes without a server.
- Existing uncovered production paths receive focused unit tests; CI coverage uses `--cov-fail-under=100`.
- `.github/workflows/demo.yml` runs only on the weekday schedule or manual dispatch. Scheduled runs require repository variable `PYMT5_DEMO_ENABLED=1`; the job uses the protected `demo` environment and secrets `PYMT5_LOGIN`/`PYMT5_PASSWORD`, with optional server/symbol variables.
- `README.md`, `README.zh-CN.md`, `docs/development.rst`, and `docs/zh_CN/development.rst` document setup, timing, commands, coverage, and the final-unsubscribe limitation.
- Acceptance documentation distinguishes offline, live-passed, expected server rejection, skipped, and unexecuted remote GitHub runs.

## Builder latitude and constraints

Choose the smallest existing test files/helpers for coverage additions. Preserve public APIs unless a new failing test identifies a separate source defect; investigate any such defect before changing it. Use Python 3.11-compatible code, Ruff's 120-character limit, and existing pytest-asyncio patterns. Do not commit or push without explicit authorization; local CI-equivalent commands must not be reported as a remote GitHub Actions run.
