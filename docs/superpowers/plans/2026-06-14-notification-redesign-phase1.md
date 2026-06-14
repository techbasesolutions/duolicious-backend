# Notification Redesign — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline) or subagent-driven-development. Steps use `- [ ]`.

**Goal:** Kill the ugly, redundant new-message email. Replace it with one brand-shell email, and make the email cron a push-aware, preference-aware, *batched* fallback — so web-push subscribers get push only, and everyone else (incl. iOS-browser users) gets a clean branded email instead of nothing.

**Architecture:** Keep the cron's existing drift-based batching (`do_send_notification` + `update_last_notification_time`). Change only the push-vs-email *decision* and the email *template*. No DB migration in Phase 1 (uses the existing `notification_preference.push_messages` column + `push_subscription` rows).

**Tech Stack:** Flask/psycopg backend (`ahavah-api`, deploys via GHA on push to `ahavah/main`); brand email shell `emails/base.py`; title PNGs hosted from `ahavah-web/public/email/` (served at `https://ahavah.app/email/...`).

Spec: `docs/superpowers/specs/2026-06-14-notification-system-redesign-design.md`.

---

## File map (Phase 1)

- Create: `emails/notification.py` — pure brand-shell new-message email (no app imports beyond `base`).
- Create: `ahavah-web/public/email/title-message.png` + `title-message-wht.png` — Ultra title images.
- Modify: `service/cron/notifications/sql/__init__.py` — add `has_live_push` + `push_messages` to `Q_UNREAD_INBOX`.
- Modify: `service/cron/notifications/__init__.py` — push-aware fallback decision; drop the dead mobile/`token` branch; use the new email.
- Modify: `service/cron/notifications/template/__init__.py` — delete `emailtemplate` + `little_part` (ugly HTML); KEEP `big_part` + `frequency_url` (reused as copy + unsubscribe).

---

### Task 1: Brand-shell new-message email

**Files:** Create `emails/notification.py`.

- [ ] **Step 1 — implement** (pure presentation; caller passes strings):

```python
"""Transactional notification emails on the brand shell (emails/base.py).

Phase 1 covers the new-message fallback only. Other events (match,
verification) will add sibling builders here in later phases."""
from __future__ import annotations

from emails.base import render, chip, title_image, button, INK_SOFT, MUTED


def new_message_email(*, headline: str, open_url: str, unsubscribe_url: str) -> str:
    body = f"""
      {chip("New message")}
      {title_image("title-message.png", "title-message-wht.png", headline, 460)}
      <p class="e-text" style="margin:0 0 26px;font-size:16px;line-height:1.55;color:{INK_SOFT};">
        {headline} Open Ahavah to read and reply.
      </p>
      {button("Open Ahavah →", open_url, variant="lime", full=True)}
    """
    footer = f"""
      You're getting this because you have unread messages on Ahavah.
      <br/>
      <a href="{unsubscribe_url}" style="color:{MUTED};text-decoration:underline;">Turn off message emails</a>
    """
    return render(
        title="You have a new message on Ahavah",
        preheader="Someone is waiting to hear back from you.",
        body_html=body,
        footer_html=footer,
    )
```

- [ ] **Step 2 — verify import + render** (no app/DB deps):

```bash
python -c "from emails.notification import new_message_email; \
h=new_message_email(headline='You have a new message.', open_url='https://ahavah.app/', unsubscribe_url='https://api.ahavah.app/update-notifications?type=Every&frequency=Never'); \
print('#70f' not in h, 'e-card' in h, 'title-message' in h)"
```
Expected: `True True True` (no legacy purple; uses brand card; references the title image).

- [ ] **Step 3 — commit** (after Task 2 renders the PNGs so the image resolves).

---

### Task 2: Ultra title images

**Files:** Create `ahavah-web/public/email/title-message.png`, `title-message-wht.png`.

- [ ] **Step 1 — render** using the existing Ultra title-PNG convention (same script used for `title-welcome.png` / `title-community.png`). Text: **"You've got mail."** (or "New message." — pick what renders cleanly at 460px). Light = ink `#0F0B1F` on transparent; dark = white on transparent; 2x raster.

```bash
# locate the renderer (scripts/render-title-png.mjs in ahavah-web or ahavah-api)
node scripts/render-title-png.mjs --text "You've got mail." --out public/email/title-message.png --ink "#0F0B1F"
node scripts/render-title-png.mjs --text "You've got mail." --out public/email/title-message-wht.png --ink "#FFFFFF"
```
(If the script's flags differ, mirror exactly how `title-community.png` was produced earlier this session.)

- [ ] **Step 2 — verify** both PNGs exist, are transparent, non-empty (`ls -la`, open the light one).

- [ ] **Step 3 — commit + deploy ahavah-web** (Vercel on push). Confirm `https://ahavah.app/email/title-message.png` returns 200 image.

---

### Task 3: Unread query carries push reachability + message-push pref

**Files:** Modify `service/cron/notifications/sql/__init__.py`.

- [ ] **Step 1 — add two computed columns** to `inbox_second_pass` (after `person.email,` ~line 76) and to the final `SELECT`:

In `inbox_second_pass` SELECT list add:
```sql
        EXISTS (
            SELECT 1 FROM push_subscription ps WHERE ps.person_id = person.id
        ) AS has_live_push,
        COALESCE(
            (SELECT np.push_messages FROM notification_preference np
              WHERE np.person_id = person.id),
            TRUE
        ) AS push_messages,
```
In the final `SELECT` (after `intros_drift_seconds`) add:
```sql
    ,has_live_push
    ,push_messages
```

- [ ] **Step 2 — verify** the query runs (read-only) against prod and returns the new columns for a candidate (or empty set if none pending):
```bash
ssh ... "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \"SELECT has_live_push, push_messages FROM (<paste Q_UNREAD_INBOX>) q LIMIT 5;\""
```
Expected: columns present, booleans.

---

### Task 4: Push-aware batched fallback cron

**Files:** Modify `service/cron/notifications/__init__.py`.

- [ ] **Step 1 — extend the dataclass** (`PersonNotification`): add fields, remove the now-dead `token`:
```python
    has_intro: bool
    has_chat: bool
    name: str
    email: str
    chats_drift_seconds: int
    intros_drift_seconds: int
    has_live_push: bool
    push_messages: bool
```
(Delete the `token: str | None` line.)

- [ ] **Step 2 — rewrite `send_notification`** (the cron is now the EMAIL FALLBACK; real-time web push already fires in `service/chat/messagestorage`):
```python
async def send_notification(row: PersonNotification):
    sketch = f"person_uuid={row.person_uuid} intro={row.has_intro} chat={row.has_chat}"
    # Web push already covers reachable users in real time. Only email the
    # ones push can't reach (no live subscription, or message-push disabled).
    if row.has_live_push and row.push_messages:
        print('Push-reachable; skipping email:', sketch)
        return
    print('Sending email notification:', sketch)
    await send_email_notification(row)
```

- [ ] **Step 3 — point `send_email_notification` at the new template:**
```python
from emails.notification import new_message_email
from service.config import WEB_BASE_URL, API_BASE_URL  # API_BASE_URL for unsub
from service.cron.notifications.template import big_part, frequency_url

async def send_email_notification(row: PersonNotification):
    if not do_send_email_notification(row):
        print('Email notification suppressed (example.com):',
              f'person_uuid={row.person_uuid}')
        return
    unsubscribe_url = frequency_url(row.email, 'Every', 'Never')
    send_args = dict(
        subject="You have a new message on Ahavah",
        body=new_message_email(
            headline=big_part(row.has_intro, row.has_chat),
            open_url=f"{WEB_BASE_URL}/inbox",
            unsubscribe_url=unsubscribe_url,
        ),
        to_addr=row.email,
        list_unsubscribe=f"<{unsubscribe_url}>",
    )
    aws_smtp = make_aws_smtp()
    await asyncio.to_thread(aws_smtp.send, **send_args)
```
(Confirm `aws_smtp.send` accepts `list_unsubscribe`; smtp module supports RFC-8058 — see `smtp/__init__.py`. If the kwarg name differs, match it.)

- [ ] **Step 4 — delete the dead mobile path:** remove `send_mobile_notification`, `disable_mobile_notifications`, the `_disable_mobile_notifications_file` constant, `import notify`, and the `from ...template import (big_part, emailtemplate)` → change to import `big_part, frequency_url` only. Remove the `Q_DELETE_MOBILE_TOKEN` import (now unused). Keep `do_send_notification` / `do_send_email_notification` / `maybe_send_notification` / `update_last_notification_time` unchanged (batching preserved).

- [ ] **Step 5 — verify imports + module loads:**
```bash
python -c "import service.cron.notifications" 2>&1 | tail -3   # no ImportError
```

---

### Task 5: Gut the old template (keep the reused helpers)

**Files:** Modify `service/cron/notifications/template/__init__.py`.

- [ ] **Step 1 — delete `emailtemplate(...)` and `little_part(...)`** (the off-brand HTML + its helper). KEEP `big_part` and `frequency_url` (reused by the cron). Remove now-unused imports (`PRODUCT_NAME`, `EMAIL_DOMAIN`, `WEB_BASE_URL` if orphaned; keep `API_BASE_URL` for `frequency_url`, `urlencode`).

- [ ] **Step 2 — verify** nothing else imports `emailtemplate`/`little_part`:
```bash
grep -rn "emailtemplate\|little_part" service/ emails/ --include=*.py
```
Expected: no references (besides the deletion).

---

### Task 6: Deploy + verify (success criteria)

- [ ] **Render check (local):** render `new_message_email(...)` to an `.html`, screenshot in headless Chrome **light + dark** → brand card, Ultra title image, lime CTA, NO `#70f`. (Per `feedback_verify_rendered_pixels`.)
- [ ] **Deploy backend:** push `ahavah/main`; cron container rebuilds.
- [ ] **Behavior check on prod:**
  - A user **with** a live push subscription + `push_messages` ON → cron logs "Push-reachable; skipping email" → **no email**.
  - A user **without** a subscription → cron sends the **new brand email** (verify via a test send to a non-suppressed inbox, e.g. `harrigan.tennyson@gmail.com`).
  - `https://ahavah.app/email/title-message.png` → 200.
- [ ] **Success = ** subscribers no longer get the message email; non-subscribers get the on-brand one; no `#70f` anywhere.

---

## Phase 2 (outline — needs `/frontend-design` for the new screen)

- Migration `00NN_notification_channel_prefs.sql`: add `email_*` columns + the two new-event push/email columns; drop-reserve `push_weekly_digest`.
- Extend `GET/PATCH /notifications/preferences` + `useNotificationPreferences()` to the full per-event × per-channel set.
- **New dedicated Notifications screen** (Profile entry; retire `/settings/notifications`): per-device push status + "send test" + the events×Push|Email matrix. → run `/frontend-design` then `/ui-implementer` (kit primitives only).
- Cron honors `email_messages` (not just push-reachability).
- Dedicated notification unsubscribe token endpoint (`/notifications/unsubscribe`) mapping to `email_<event>` (supersedes the Phase-1 `update-notifications` reuse).

## Phase 3 (outline)

- Wire `verification` (selfie result handlers + `/webhooks/stripe-identity`) and `likes` (`service/decisions`) through `notify()`; email builders in `emails/notification.py`.
- Subscription failure-pruning (consecutive-failure counter) + `POST /notifications/test`.
- `profile_views` LAST: view-tracking + 24h per-viewer throttle + identity-blind. Removable in isolation if noisy.

## Self-review

- **Spec coverage:** Phase-1 tasks cover spec §6 (email redesign), §5 (push-aware batched fallback), and the §1 double-notify fix. §4 migration, §9 screen, §7 reliability, §8 unsubscribe-token, and the verification/likes/profile-views events are explicitly Phase 2/3.
- **Approximation honored:** "push landed" = `has_live_push AND push_messages` (spec §5/§12) — implemented exactly in Task 4 Step 2.
- **No placeholders:** all steps carry concrete code/commands. The one runtime check to confirm during execution is the `aws_smtp.send` unsubscribe kwarg name (Task 4 Step 3) and the title-PNG render script's flags (Task 2) — both flagged inline.
- **Batching preserved:** `do_send_notification` (drift) + `update_last_notification_time` untouched.
