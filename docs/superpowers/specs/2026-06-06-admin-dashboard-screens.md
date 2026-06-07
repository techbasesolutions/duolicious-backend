# Ahavah Admin Dashboard — Screen Specifications

> **Purpose:** Comprehensive screen-by-screen brief for Claude Design (or any UI designer) to produce mockups of the Ahavah Admin Dashboard. Each section below is a self-contained prompt — paste it into Claude Design and ask for that single screen.

---

## Project context (read first)

**What this is:** An internal-only admin dashboard for the Ahavah PWA (Torah-observant matchmaking app). Used by staff to see and manage app users, the user-related functions (entitlements, tokens, roles, lifecycle), and app usage trends + statistics. Live at `admin.ahavah.app` as its own Next.js project, separate from the main app at `ahavah.app`.

**Tech stack:**
- Next.js 16 (App Router, Turbopack) + React 19
- Tailwind v4
- shadcn/ui primitives (Button, Card, Badge, Avatar, Pill, Dialog/Sheet, Input, Select, Tabs, Table, Chart placeholders)
- Optionally: Kibo UI blocks (Pill, Status, etc) for richer components
- Plus Jakarta Sans (body) + Ultra (display) — matches main app

**Theme:** Dark by default. Light mode optional. Brand tokens:

| Token | Hex | Use |
|---|---|---|
| Persian Indigo | `#5524F5` | Primary brand, signed-in surfaces background |
| Mindaro Lime | `#D7FF81` | Primary CTAs, success states |
| Lavender | `#BC96FF` | Accents, decorative |
| Pinkish Red | `#FF4566` | Destructive actions, errors |
| Ink | `#0F0B1F` | Text on light surfaces |
| Canvas | `#FAF7EE` (light) / `#0B0820` (dark) | Page background |
| Card | `#FFFFFF` / `#15121F` | Surface elevation |

**Layout posture:**
- **Desktop primary** (`≥768px` viewport): full sidebar + main content
- **Mobile fallback** (`<768px`): tabs collapse into a top scrollable strip; drawers become full-screen sheets; **destructive action buttons are hidden on mobile** (read-only)

**Personality:** clean, dense, slightly editorial. Think Linear / Vercel admin / Plain.com — not consumer-cute, not enterprise-stuffy. Tables and stats are the heroes.

---

## Shared shell (use on every screen except #1 and #2)

A single layout shell wraps every authenticated screen. Design this once and reuse:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  HEADER  │  Ahavah Admin (logo lockup)           admin@…   sign-out      │
├─────────┬──────────────────────────────────────────────────────────────────┤
│ SIDEBAR │                                                                  │
│         │                                                                  │
│ ◆ Overview                  ← active tab indicator                         │
│ ○ Users                                                                    │
│ ○ Cohorts                          MAIN CONTENT AREA                       │
│ ○ Economy                          (the per-tab screen content)            │
│ ○ Moderation                                                               │
│ ○ System                                                                   │
│ ○ Audit                                                                    │
│         │                                                                  │
│         │                                                                  │
└─────────┴──────────────────────────────────────────────────────────────────┘
```

- Header: 56px tall, dark, hairline bottom border. Logo on left, admin email + sign-out on right.
- Sidebar: 240px wide on desktop, collapsible to icon-only at `lg-`. Lavender accent on active tab.
- Main content: `padding: 32px 40px;` with max content width 1400px, centered.
- The sidebar nav items map 1:1 to URL params (`?tab=overview`, etc).

---

# Screens to design

There are **10 screens**. Each section below is a stand-alone brief — give it to Claude Design and you'll get back a mockup.

---

## Screen 1 — Sign-in

**Route:** `admin.ahavah.app/auth/sign-in`

**Purpose:** Email + OTP login for admins. No password.

**Flow:**
1. User enters their email → presses "Send code"
2. Backend sends 6-digit OTP to their email
3. UI shows OTP input
4. User enters code → presses "Sign in"
5. Backend sets `session_token` cookie → app routes to `/`

**Layout:** centered single column, max-width 400px on a full-bleed dark canvas. No sidebar (this is pre-auth).

**Content:**
- Top: small Ahavah Admin lockup (logo + wordmark)
- Heading: "Sign in to the admin panel"
- Body: "Enter the email associated with your admin account."
- Email input (autofocus on load)
- "Send code" lime CTA, full-width
- After "Send code" succeeds: replace form with OTP input + "Sign in" CTA
- Footer link: "← Back to ahavah.app" (small, muted)

**States:**
- Empty (initial): just the email form
- Loading: spinner inside CTA
- Code-sent: email form replaced with OTP form, "We sent a code to <email>"
- Invalid email: inline red error under input
- Invalid OTP: inline red error under OTP input + "Try again"
- Network error: toast at bottom
- Non-admin signed in: redirect to Screen 2 (Unauthorized)

---

## Screen 2 — Unauthorized

**Route:** any admin route, accessed by a signed-in non-admin

**Purpose:** Tell a signed-in user that they don't have admin access. Apologetic but firm.

**Layout:** centered single column, no sidebar.

**Content:**
- Icon at top: shield-x or lock-x (lucide, lavender-tinted, 64px)
- Heading: "You don't have access here"
- Body: "Your account isn't authorized to use the admin panel. If you think this is a mistake, contact the team."
- Two side-by-side buttons:
  - Primary (lavender): "Sign out"
  - Secondary (ghost): "Return to ahavah.app"

**States:** static. No interactive states other than the two buttons.

---

## Screen 3 — Overview (default tab)

**Route:** `admin.ahavah.app/?tab=overview` (default tab on app load)

**Purpose:** One-glance health of the entire app. The first thing an admin sees every day.

**Layout:** uses the shared shell. Main content area divided into 3 vertical stacks:

```
┌─ KPI GRID (3 cols × 2 rows = 6 cards) ─────────────────────────────────┐
│  [Signups today]  [Active 24h]  [Total persons]                        │
│  [Premium holders] [Pending reports] [Referral signups 7d]             │
└────────────────────────────────────────────────────────────────────────┘
┌─ CHARTS ROW (3 cols, equal width) ─────────────────────────────────────┐
│  [Signups 30d]    [Click-through 7d]    [Premium adds 30d]             │
│   (stacked area)   (line chart)          (bar chart)                   │
└────────────────────────────────────────────────────────────────────────┘
┌─ RECENT ACTIVITY FEED (full width) ────────────────────────────────────┐
│  Reverse-chrono list of last 20 notable events:                        │
│   • 14:23 - harrigan.tennyson@gmail.com signed up                      │
│   • 14:18 - 1 photo flagged for review                                 │
│   • 14:05 - referral attribution: alice → bob                         │
│   etc.                                                                 │
└────────────────────────────────────────────────────────────────────────┘
```

**KPI card structure** (used throughout the dashboard):
- Small uppercase label at top (e.g. "SIGNUPS TODAY", `text-meta`, muted)
- Large number (Ultra display font, ~36px)
- Tiny delta vs yesterday: "+3 vs yesterday" green, "-2 vs yesterday" red
- Optional sparkline at the bottom of the card (7-day trend)

**Chart panels:**
- 240px tall each
- Title at top + small time-range pill (24h / 7d / 30d / all)
- Empty state: "No data yet" centered when underlying table is empty (very common pre-launch)

**Recent activity feed:**
- Timestamp on left (HH:MM, or "yesterday" if older)
- Event line in the middle
- Right side: small icon indicating event type (signup, abuse, referral, premium, etc.)
- Each row clickable → opens the relevant drawer (user drawer for signups, report drawer for abuse, etc.)

---

## Screen 4 — Users tab

**Route:** `?tab=users`

**Purpose:** Search, filter, and browse the full population (person + waitlist_signup + beta_signup, unified by email). Drill into one user by clicking a row.

**Layout:** uses the shared shell. Main content:

```
┌─ Search + filters row ─────────────────────────────────────────────────┐
│  [🔍 Search email, name, uuid prefix...]   [×]                          │
│                                                                         │
│  Filters:  All  Has person  Waitlist only  Beta only                   │
│            Unsubscribed  Has premium  Role=admin  Role=mod              │
│  (pill buttons, multi-select, active pills filled with lavender bg)     │
└─────────────────────────────────────────────────────────────────────────┘
┌─ Results table ─────────────────────────────────────────────────────────┐
│ [☐]  Avatar  Name              Email                Joined    Roles    │
│      [img]   Ehud Harrigan     harrigan…@gmail.com  May 27    ─        │
│      [img]   Abby Thies        abbythies@gmail.com  Jun 4     ─        │
│      ...                                                                │
│      19 more rows                                                       │
│                                                                         │
│  [< Prev]  page 1 of 3 (47 users)  [Next >]                            │
└─────────────────────────────────────────────────────────────────────────┘
```

**Row fields:**
- Avatar (32px round, fallback to monogram if no photo)
- Display name (or "—" if waitlist-only)
- Email (truncated with ellipsis at ~28 chars, full on hover)
- Joined date (e.g. "May 27" or "Jun 4")
- Roles (pills: "admin", "mod", or "—")
- Status (small color dot: green = active, red = deactivated, yellow = pending-deletion)

**Interactions:**
- Click a row → opens the User drawer (Screen 5)
- Search debounced 300ms
- Filter pills are toggleable; combinations are AND'd
- Pagination: 20 rows per page

**Empty state:** "No users match these filters" + a "Clear filters" link

---

## Screen 5 — User drawer (overlay)

**Route:** any screen + `?user=<uuid>` → drawer is open

**Purpose:** Drill into one user. The most data-dense screen.

**Layout:** slide-in from right (desktop, 720px wide) or full-screen sheet (mobile). Has its own internal tab strip so it's not one enormous scroll.

```
┌─ DRAWER HEADER (sticky) ────────────────────────────────────────────┐
│  [×]   [avatar]  Ehud Harrigan                                       │
│                  harrigan.tennyson@gmail.com                         │
│                  uuid 1ea2acc2…  •  joined May 27  •  ⚙ admin       │
│         badges: [active] [premium] [admin]                          │
├──────────────────────────────────────────────────────────────────────┤
│  Identity   Activity   Economy   Actions   ← internal tabs           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│                  [content for selected internal tab]                 │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Internal tabs:**

### Identity (default)
- Sub-section: **Profile**
  - Name, date_of_birth, gender, country, region, languages, primary_language, bio (collapsible if long)
- Sub-section: **Photos**
  - 6-slot grid (matches the app's photo layout); each slot shows the photo + moderation state badge (approved / pending / rejected)
- Sub-section: **Waitlist answers** (if applicable)
  - Tabular display of the demographic answers they gave

### Activity
- Sign-up time, last sign-in, sign_in_count
- Last online (relative time)
- Sessions opened in last 7d (small line chart)
- Recent actions (likes given, messages sent, photos uploaded — last 10 each, expandable)

### Economy
- **Entitlements** sub-section
  - Card: each entitlement with name + expiry + revoke button
  - "Grant entitlement" button → opens small dialog with name select + expiry picker + reason field
- **Tokens** sub-section
  - Current balance (large number)
  - Manual credit/debit buttons → opens dialog with delta + reason
  - Last 20 ledger rows in a small table (delta, reason, when)

### Actions (the destructive stuff)
- **Account lifecycle**
  - Deactivate button (orange) — confirms "Soft delete. User can't sign in but data is preserved. Reversible."
  - Reactivate button (grey, only shown if deactivated)
  - Hard delete button (red) — REQUIRES typing the user's email to confirm + double-confirm dialog
- **Roles**
  - Current roles as pills + grant/revoke buttons for `admin` and `mod`
- **Support / debug**
  - Resend OTP button — confirms "This will send a new sign-in code to the user. They should expect an email in <1 minute."
  - Force-clear stuck onboardee button — confirms "Deletes their onboardee + active sessions. They'll start onboarding from scratch."
- **Audit trail for this user** (read-only at bottom)
  - Table of every admin action against this user

**All mutating buttons require:** explicit confirmation dialog with the action name + target email + reason field (free-text). Every action writes an `admin_audit_log` row.

---

## Screen 6 — Cohorts tab

**Route:** `?tab=cohorts&sub=waitlist|beta|referrals`

**Purpose:** Pre/post-launch funnel + demographics views.

**Layout:**

```
┌─ Sub-tab strip ────────────────────────────────────────────────────────┐
│  ◆ Waitlist   ○ Beta   ○ Referrals                                     │
├────────────────────────────────────────────────────────────────────────┤
│  [time-range selector: 24h / 7d / 30d / all]                            │
│                                                                         │
│  [main chart for selected sub-tab]                                      │
│                                                                         │
│  [breakdown tables / additional charts below]                           │
└────────────────────────────────────────────────────────────────────────┘
```

**Waitlist sub-tab content:**
- Top KPI strip: total signups, completed (with answers), empty rows, today, this week
- Main chart: daily signups stacked area (waitlist vs beta vs person)
- Breakdown row: sex split, intent multi-select counts, family preference counts, top countries (horizontal bar), top ethnicities, top assemblies
- "How they found us" pie/bar: source distribution from `referral_source`

**Beta sub-tab content:**
- Top KPI strip: cohort size, completed onboarding, referral codes minted, intro email sent, never signed in
- Table: per-tester row with email, code, completed?, intro-sent at, last sign-in
- Funnel: waitlist → beta → person → completed (4-step horizontal funnel chart)

**Referrals sub-tab content:**
- Top KPI strip: total link clicks, distinct clickers, signups via link, credits fired
- Click-stream chart: clicks/day by UA class (mobile / desktop / bot)
- Per-inviter table: inviter email, clicks, signups, credited count, pending balance
- Drill-down: click an inviter row → expands to show their full click + referral history

---

## Screen 7 — Economy tab

**Route:** `?tab=economy&sub=ledger|entitlements|subscriptions`

**Purpose:** Token economy + Premium / Stripe admin.

**Layout:** same shell + sub-tab strip pattern as Cohorts.

**Token Ledger sub-tab content:**
- Top KPI strip: total tokens in circulation, total credits today, total debits today, total spent today
- Filter row: reason (`purchase`, `subscription_stipend`, `reveal_liker`, `super_like`, `day_pass`, `boost`, `refund`, `referral`, `admin_credit`, `admin_debit`), date range, person email/uuid
- Paginated table: person, delta (green/red), reason, metadata (JSON, truncated with hover), created_at
- Click a row → opens the User drawer scrolled to Economy tab

**Entitlement Holders sub-tab content:**
- Filter: entitlement name (default `premium`)
- Filter: expires within N days (presets: 7 / 30 / 90)
- Filter: "founding members only" toggle
- Table: person, entitlement, expires_at, time-left (countdown badge), source ("Stripe" / "admin grant" / "founding-member" / "stipend")
- Click row → User drawer

**Subscriptions sub-tab content:**
- KPI strip: active, past_due, canceled, total recurring revenue (USD/mo)
- Status distribution: small donut chart
- Table: person, Stripe subscription id, status, current_period_end, next_invoice_amount, source price_id

**Pre-Stripe state:** all subscription stats show "0" / "no data yet" with friendly empty state.

---

## Screen 8 — Moderation tab

**Route:** `?tab=moderation&sub=reports|photos|messages`

**Purpose:** Abuse handling. Extension of the existing `/admin/reports` surface.

**Layout:** same shell + sub-tab pattern.

**Reports sub-tab content:**
- Lift the EXISTING `/admin/reports` content here verbatim (it works today)
- Add: filter by status (unresolved / resolved / dismissed) + reason (when classification is added)
- Add: per-report drawer or expandable row with full context

**Photos sub-tab content:**
- Default: pending-review photos (nsfw_score borderline, cron didn't auto-decide)
- Filter: state = pending / rejected / approved
- Grid view: 4 across on desktop, 2 on mobile
- Each card: photo thumbnail, owner email (small), uploaded_at, nsfw_score, two actions: ✓ approve / ✗ reject (with reason)
- Click photo → fullscreen lightbox with owner detail panel

**Rude Messages sub-tab content:**
- List of messages flagged as rude (existing antiabuse pipeline)
- Each row: from email, to email, snippet, flagged_at, action (warn / suppress / nothing)
- Expandable to show full message thread context (last 10 messages around the flag)

---

## Screen 9 — System tab

**Route:** `?tab=system`

**Purpose:** Operational health snapshot. Fits on one screen.

**Layout:** dense single-screen dashboard, no sub-tabs.

**Sections (each is a card):**

1. **Signups** (over time)
   - Counts: today / 7d / 30d
   - Mini line chart: signups by day

2. **Active users**
   - DAU / WAU / MAU as 3 numbers
   - Mini bar chart: active count by day

3. **OTP deliverability**
   - Success rate (24h): 97.3%
   - Total sent (24h): 142
   - Failures (24h): 4 (expandable to list failures)

4. **API errors**
   - 5xx count (24h): N
   - Top failing endpoints (24h): table
   - **Note:** requires log pipeline that may not exist yet → render empty state with "Sentry not yet wired" hint

5. **Database**
   - Row counts per major table: person, waitlist_signup, beta_signup, photo, message, etc.
   - Listed as small key-value pairs

6. **Deploy state**
   - Backend: latest GHA run + SHA + timestamp
   - Frontend: latest Vercel deploy + SHA + timestamp
   - Cron container uptime

---

## Screen 10 — Audit log tab

**Route:** `?tab=audit`

**Purpose:** Who-did-what trail. Every destructive admin action lives here.

**Layout:** filterable table.

**Filter row:**
- Actor (admin email autocomplete)
- Target (user email autocomplete)
- Action type (multi-select dropdown: `grant_entitlement`, `revoke_entitlement`, `manual_credit`, `manual_debit`, `grant_role`, `revoke_role`, `deactivate`, `reactivate`, `hard_delete`, `clear_onboardee`, `resend_otp`)
- Date range (preset chips + custom range)

**Table:**
- When (timestamp, reverse chrono)
- Actor (admin email + avatar)
- Action (action type pill, color-coded by severity)
- Target (user email + avatar, clickable → User drawer)
- Metadata (truncated JSON, expandable to full)

**Empty state:** "No admin actions in this range" + suggestion to broaden the filter.

**Export:** v1 has no built-in CSV export. Add a small note: "Run the SQL in `service/admin/sql.py` directly for ad-hoc exports."

---

## Cross-cutting design notes

These apply to every screen and are worth giving Claude Design upfront:

### Density vs whitespace
Lean dense. This is an internal tool — admins want to see a lot at once. Tables should be `compact` density by default (32-36px row height). Whitespace is for breathing room around major sections, not within tables.

### Loading states
Use shadcn's `Skeleton` primitive (or equivalent) for tables and cards. Don't show centered spinners — those feel slow.

### Empty states
Almost every chart will be empty pre-launch. Always have a graceful empty state: small icon + 1-sentence explanation + (if applicable) a "what to do next" hint.

### Confirmation dialogs
Every destructive action goes through `<Dialog>` (shadcn) with:
- Title: the action ("Hard delete account")
- Target identity: email + uuid
- Required text-confirmation for irreversible actions (type the email)
- Required reason field (free-text)
- Two buttons: Cancel (ghost) + Confirm (destructive red)

### Toasts
Use sonner (already in stack). Success toast for completed admin actions, error toast for failures. Mid-screen, top-center.

### Responsive
- `lg+` (1024px+): full sidebar always visible
- `md` (768-1023px): sidebar collapses to icons; expand on hover
- `<md` (mobile): sidebar becomes a hamburger drawer. **Destructive action buttons hidden.** Read-only view of everything else.

### Keyboard
- `/` focuses search (on any tab with search)
- `Esc` closes the user drawer / any dialog
- `Cmd/Ctrl+K` opens a command palette (deferred for v1 but worth noting for v2)

### Accessibility
- All interactive elements: visible focus ring (lime, 2px, offset 2px)
- All charts: include a "View as table" toggle for screen-reader users
- Color is never the only signal — pair every color with an icon or text

---

## Suggested prompt template for Claude Design

When asking Claude Design to design any of the 10 screens, paste:

```
PROJECT: Ahavah Admin Dashboard
SCREEN: <screen name from above, e.g. "Screen 4 — Users tab">

STACK: Next.js 16 + React 19 + Tailwind v4 + shadcn primitives.
THEME: Dark default. Brand tokens:
  - Persian Indigo #5524F5
  - Mindaro Lime #D7FF81 (primary CTAs)
  - Lavender #BC96FF (accents)
  - Pinkish Red #FF4566 (destructive)
TYPE: Plus Jakarta Sans body, Ultra display.
PERSONALITY: Internal tool. Linear/Vercel admin/Plain.com aesthetic — clean, dense, slightly editorial. Not consumer-cute.
LAYOUT: Desktop-primary (>=768px). Mobile is a read-only fallback with destructive buttons hidden.

<paste the full section for that screen from the screens doc>

Deliverable: a high-fidelity mockup of this screen in dark mode, desktop viewport (1440x900), AND a mobile viewport (390x844). Include all empty / loading / error states inline as annotations.
```

---

## Out of scope (don't design these in v1)

- Bulk actions (no "select 50 users and grant premium")
- Real-time / live updates / WebSocket
- Profile editing (admin can't change a user's name, photos, bio)
- CSV / data export UI
- Stripe Customer Portal deep-link
- In-app SQL runner
- Admin-initiated user invites or magic links
- Multi-admin / multi-tenant scoping
- 5xx pipeline / log aggregation (until Sentry-ish is wired)

These can be designed later when the v1 surface ships and you find out which of them are actually needed.

---

**End of screens spec.** 10 designable surfaces, ready to feed to Claude Design one at a time.
