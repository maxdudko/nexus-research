# Phase 9 Gate C — Report experience and polish

**Status:** Not approved  
**Date:** 2026-10-23  
**Branch:** `development` at `8ac8e8b`, with the Group C working tree uncommitted  
**Normative contract:** [ADR-009](../../adr/ADR-009.md) C8. This file records what was observed. It does not tag `v0.2.0`, close Phase 9, or approve a public deployment.

The invited-user walkthrough was not run. There was no signed-in session and no paid provider call. Those checklist items stay open. The Phase 9 roadmap status is unchanged.

## Observed

From `apps/web` on 2026-10-23:

- `npm run lint` passed.
- `npm run typecheck` passed.
- `npm test` passed: 19 files, 101 tests.
- `npm run build` passed.

The web tests cover stored run statuses, a present report, the citation caveat, unsafe URLs, PDF page labels, limitations, 401, 404, a 503 message, a run-id change that drops the previous question, dashboard copy with no scope mint, the idle new-research sentence, stage announcements outside the heartbeat line, and a user-id change that remounts page content.

Anonymous browser check on the local dev server at `http://127.0.0.1:3000`:

- `/research/11111111-1111-4111-8111-111111111111` showed “Checking session.” and then `/login?next=%2Fresearch%2F11111111-1111-4111-8111-111111111111`. The login page text was the sign-in form. The question was not shown.
- At 320 CSS px, that login page had `scrollWidth` and `clientWidth` of 320. Email, password, and Log in were present.

Contrast of the existing tokens, computed against WCAG relative luminance, is above 4.5:1 for ink, muted, and danger on canvas, and for on-primary on primary, in both themes. Disabled text is quieter and was left unchanged. No token was edited.

API integration tests and packaged smoke were not rerun. This group does not change the proxy.

## Checklist

- [ ] Core routes and the app shell work at desktop and 320 CSS px. Login at 320 was checked. Signed-in routes were not.
- [ ] An invited user can start a web run and read a persisted cited report.
- [ ] An invited user can attach a text PDF and understand indexing and citation limits.
- [ ] Stop, disconnect, partial, insufficient, failed, and stale-running states are truthful in a manual run.
- [ ] History pagination and a direct report link work after refresh for a signed-in user.
- [ ] Account A cannot see account B’s research through the UI and API in this review. Logout and account switch clear private state in a manual session.
- [ ] Keyboard navigation, labels, focus, and status or error announcements pass a manual review of the signed-in product.
- [ ] Web lint, typecheck, test, and build pass, and API integration plus packaged smoke pass on the reviewed commit. Web checks passed on the working tree. API integration and smoke were not rerun.
- [ ] Known limitations and this evidence are recorded, and a reviewer has accepted Gate C.

## Not claimed

- No operator completed the spec’s two-account walkthrough.
- No real provider call was made.
- Phase 9 is not complete.
