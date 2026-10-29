# Plan: "Claim This Profile" → identity, resume, and self-added results

> Status: **Phases 0-5 implemented 2026-10-23.** Migration 012 (dropping
> `users.sailor_id`) is deliberately deferred -- see Migration sequence below.
>
> Goal: once a sailor claims a profile, store their name + email, let them turn their
> results into a resume, and let them add results the scrapers missed.
>
> Decisions taken as implemented: **verification tier 2** (verified email,
> self-serve, reversible) and **manual results labelled-but-ungated**. Both are
> the recommendations this plan originally made; change either by editing the
> relevant phase and the config flags in `config.py`.

---

## What exists today (and what's broken)

| Piece | State |
|---|---|
| `User.sailor_id` → `Sailor` | Works, but **one** profile only, and ClubSpot-only |
| `SailorName.is_claimed` / `user_id` / `claimed_at` | Columns exist, **nothing ever writes them** |
| `/claim-profile` (`app.py:936`) | Only lists/claims ClubSpot `Sailor` rows |
| `templates/sailor_name_profile.html:24` | "Claim This Profile" links to `/claim-profile` — **a page that cannot contain them** |
| `ResumeLink.sailor_id` | `nullable=False` on `sailors` → **HS/College sailors can't build a resume at all** |
| `ResumeLink.selected_result_ids` | Flat list of `Result.id` — IDs collide across `hs_results`/`college_results`, so it can't address them |
| `User` name | **No name field** — only email/password/role |
| Manual result entry | **Does not exist** anywhere |

Three structural blockers, in order of how much they constrain everything else:

1. **One profile per user** (`User.sailor_id` is a single FK)
2. **`ResumeLink` bound to the ClubSpot table**
3. **Result IDs that aren't globally addressable**

Fix those and the requested features become small.

### Two incidental findings that bear on the plan

- `app.py:946` — `Sailor.query.get(form.sailor_id.data)` with no null check: a forged
  `sailor_id` is a 500.
- `app.py:429-431` — `/admin` and all `/api/scraper/*` routes are `@login_required` with
  **no role check**. Any registered user is an admin. This matters because Phase 3 proposes
  an admin review queue; it needs a real gate first.

---

## Phase 0 — Gate admin, patch the claim bug

Small, independent, do it first.

- [x] Add `User.is_admin` (Boolean, default False) + an `@admin_required` decorator; apply
      to `/admin*` and `/api/scraper/*`.
- [x] `get_or_404` on the claim lookup; reject claims on already-claimed profiles *before*
      mutating.
- [x] `/claim-profile` (`app.py:961`) currently does
      `Sailor.query.filter_by(is_claimed=False).all()` and renders **every row** with
      client-side JS filtering. With ClubSpot + roster data that's tens of thousands of rows
      per page load. Replace with server-side search reusing the tokenized matcher in
      `/search` (`app.py:129`).

---

## Phase 1 — Identity: name + email on the account

`User` gets:

```python
full_name        = db.Column(db.String(200))        # what they tell us
display_name     = db.Column(db.String(200))        # optional public override
grad_year        = db.Column(db.Integer)
primary_school   = db.Column(db.String(200))
email_verified   = db.Column(db.Boolean, default=False)
email_verify_token / email_verify_sent_at
profile_completed_at = db.Column(db.DateTime)
```

Flow change: today `register()` (`app.py:900`) collects email + password + role and drops
you on the index. Instead:

- [x] `RegisterForm` gains `full_name` (required).
- [x] After a successful claim, **prefill `full_name` from the claimed profile's name** if
      blank — the claimed row already has the authoritative spelling.
- [x] New `/account` page to edit name / email / grad year / school.

Email is already unique+indexed on `users`, so "store name and email" is one migration plus
two form fields. Changing email should clear `email_verified`.

---

## Phase 2 — One account, many profiles (the key change)

A real sailor is usually **several rows**: a ClubSpot `Sailor`, an HS `SailorName`, a college
`SailorName`, plus name-variant duplicates. `User.sailor_id` as a single FK can't express
that, and it's why the HS/College claim path was never wired up.

New association table — this is the hinge the rest of the plan hangs on:

```python
class ProfileClaim(db.Model):
    __tablename__ = 'profile_claims'
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # exactly one of these is set; CheckConstraint enforces it
    sailor_id      = db.Column(db.Integer, db.ForeignKey('sailors.id'), nullable=True)
    sailor_name_id = db.Column(db.Integer, db.ForeignKey('sailor_names.id'), nullable=True)

    status      = db.Column(db.String(20), default='approved')  # pending|approved|rejected
    is_primary  = db.Column(db.Boolean, default=False)   # drives the canonical display name
    evidence    = db.Column(db.Text)      # school email, grad year, note
    requested_at / decided_at / decided_by_user_id

    __table_args__ = (
        db.UniqueConstraint('sailor_id',      name='uq_claim_sailor'),       # a profile is claimable once
        db.UniqueConstraint('sailor_name_id', name='uq_claim_sailor_name'),
        db.CheckConstraint('(sailor_id IS NULL) != (sailor_name_id IS NULL)', name='ck_claim_one_target'),
    )
```

Keep `Sailor.is_claimed` / `SailorName.is_claimed` as denormalized flags written on approval
— templates already read them, so no template churn.

**Migration must backfill**: every existing `User.sailor_id` becomes an approved
`ProfileClaim` with `is_primary=True`. Keep the `User.sailor_id` column during transition so
nothing breaks mid-deploy, then drop it in migration 012 once `app.py:224`, `app.py:308`,
and `app.py:659` read through `ProfileClaim`.

**Suggested-match helper.** On claim, run the name-normalized match `sailor_profile`
(`app.py:224`) already does and offer the other-source rows: *"We also found a college record
for this name — claim it too?"* This is what turns a claim into a complete resume instead of
a half one, and it's cheap because the matching query already exists.

Ownership check becomes a single helper used everywhere:

```python
def user_claims(user) -> set[tuple[str, int]]   # {('clubspot', 12), ('sailor_name', 804)}
def user_owns(user, kind, obj_id) -> bool
```

- [x] Rewrite `/claim-profile` to accept **either** `?sailor_id=` or `?sailor_name_id=`
- [x] Fix `templates/sailor_name_profile.html:24` to pass `sailor_name_id=sailor_name.id`

---

## Phase 3 — Verification (how much to trust a claim)

Right now anyone can claim anyone. Three tiers; **recommend starting at tier 2**, because
tier 1 lets someone take a competitor's profile and tier 3 won't scale past your own
attention span.

1. **Trust-on-claim** — current behavior. Fast, abusable.
2. **Verified email + self-serve, reversible** *(recommended)* — claim is immediate and
   usable, but requires a confirmed email, logs `ProfileClaim.evidence`, and exposes an
   "this isn't me / dispute" path plus an admin unclaim. Low friction, auditable,
   recoverable.
3. **Admin approval queue** — `status='pending'`, nothing visible until approved. Highest
   trust, becomes a bottleneck.

Tier 2 needs:

- [x] An email sender (none in the repo today — add `ENABLE_EMAIL` + SMTP config to
      `config.py`, gate on it so local dev stays sendless)
- [x] A `/verify-email/<token>` route
- [x] An admin claims list at `/admin/claims`

Resume **publishing** (not building) should require `email_verified` — that's the point
where a claim becomes public.

### DECISION TAKEN: tier 2

Implemented as tier 2. Claiming is immediate; publishing a shareable resume
requires a confirmed email, gated by `REQUIRE_VERIFIED_EMAIL_TO_PUBLISH`
(default true). Email sending is off by default (`ENABLE_EMAIL=false`) and
verification links are written to the app log instead, so local development
needs no SMTP server. `flask verify-email-manually <email>` confirms an address
on a deployment that cannot send mail.

---

## Phase 4 — Self-added results

New table, owned by the user rather than by a scraped profile:

```python
class ManualResult(db.Model):
    __tablename__ = 'manual_results'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    regatta_name    = db.Column(db.String(300), nullable=False)
    regatta_date    = db.Column(db.Date, index=True)
    location        = db.Column(db.String(200))
    place_numeric   = db.Column(db.Integer)
    total_boats     = db.Column(db.Integer)
    boat_type       = db.Column(db.String(100))   # "420", "Laser"
    division        = db.Column(db.String(50))
    role            = db.Column(db.String(20))    # skipper|crew
    team_name       = db.Column(db.String(200))
    crew_partner    = db.Column(db.String(200))
    notes           = db.Column(db.Text)

    source_url      = db.Column(db.String(500))   # link to published results, if any
    verified        = db.Column(db.Boolean, default=False)   # admin-confirmed
    created_at / updated_at
```

Design points worth deciding deliberately:

- **Attach to `user_id`, not to a profile row.** A manual result is a claim about the
  person, and the person may hold several profiles. It also survives a profile being merged
  or re-scraped.
- **Mark them visibly.** `verified=False` entries render with a "self-reported" marker on
  the resume. Without that, the whole resume's credibility drops to the level of its weakest
  row — which defeats the purpose for the coaches reading it.
- **Dedupe on entry.** Before saving, fuzzy-match `regatta_name` + `regatta_date` against
  the user's scraped results and warn: *"This looks like a regatta we already have — did you
  mean to edit it?"* Otherwise resumes fill with doubles as the scraper backfills.
- **Don't let manual results touch the scraped tables.** No writes into `results` /
  `hs_results` / `college_results` — scrapers overwrite those, and a user-entered row there
  would be silently destroyed or, worse, served to other users as scraped fact.

Routes:

- [x] CRUD at `/account/results` + `/account/results/new|<id>/edit|<id>/delete`, all
      `user_owns`-gated
- [x] New `ManualResultForm` in `forms.py`

### DECISION TAKEN: labelled, not gated

Manual results publish immediately and render marked "Self-reported" on every
resume template, with a footnote explaining the label. The `verified` column
exists and an admin can set it, but it gates nothing yet -- flipping it to a
gate means filtering on `verified` in `collect_user_entries`.

---

## Phase 5 — Source-agnostic resume

This is where the current schema actually blocks you.

### 5a. Globally addressable result IDs

`selected_result_ids` becomes a list of qualified strings:

```json
["clubspot:1203", "hs:88471", "college:9912", "manual:14"]
```

Add one resolver in `utils.py` and use it in **both** `shared_resume` (`app.py:396`) and
`generate_pdf` (`utils.py:202`) — they currently duplicate the same selection query, and
that duplication is exactly how the two drift apart:

```python
RESULT_KINDS = {'clubspot': ..., 'hs': ..., 'college': ..., 'manual': ...}

def resolve_resume_entries(resume_link) -> list[ResumeEntry]:
    """Grouped per kind — 4 queries total, never N+1 — returns a normalized shape."""
```

`ResumeEntry` (dataclass) normalizes the three divergent row shapes into one:

```
date, regatta_name, place_numeric, total_boats, place_display,
boat_or_division, role, team, source_label, link, is_self_reported
```

Note the existing shape mismatch the normalizer has to absorb: `Result.placement` is an int
with no field for fleet size, while `HSResult`/`CollegeResult` carry both `place` (`"21/32"`)
and `place_numeric` + `total_boats`. Normalize toward place + total, deriving `place_display`
from them.

### 5b. ResumeLink ownership

- [x] Add `user_id` (FK users, indexed)
- [x] Make `sailor_id` **nullable**
- [x] Backfill `user_id` from `sailor.user`

Then an HS/College-only sailor can own a resume. Ownership checks move from
`current_user.sailor_id == sailor_id` to `resume_link.user_id == current_user.id`.

### 5c. Builder rebuild

- [x] `templates/resume_builder.html` becomes sectioned — ClubSpot / High School / College /
      Self-reported — with per-section select-all and a live count
- [x] Route moves from `/sailor/<id>/resume-builder` to **`/resume-builder`** (the logged-in
      user's own); keep the old URL as a redirect
- [x] The four `resume_*.html` templates and their `_pdf` twins iterate `entries` instead of
      `(result, regatta)` tuples — mechanical but non-trivial across 8 templates

---

## Migration sequence

| # | Revision | Contents |
|---|---|---|
| 008 | `add_user_identity` | `users`: `full_name`, `display_name`, `grad_year`, `primary_school`, `email_verified`, verify-token cols, `is_admin` |
| 009 | `add_profile_claims` | `profile_claims` table + **backfill from `users.sailor_id`** |
| 010 | `add_manual_results` | `manual_results` table |
| 011 | `resume_link_user_owner` | `resume_links.user_id` (backfilled), `sailor_id` → nullable, `selected_result_ids` rewritten to qualified keys |
| 012 | `drop_user_sailor_id` | **NOT WRITTEN YET, on purpose.** Every reader now goes through `ProfileClaim`, but `users.sailor_id` is still *written* (mirrored on claim/release) so a rollback to pre-009 code keeps working. Write and ship this one deploy after 008-011 are stable in production, and drop the mirroring in `_handle_claim` / `release_claim` at the same time. |

> **Renumbered 2026-10-28.** These were originally written as 007-010, but a
> pull brought an upstream `007_regatta_catalog` that also revised `006` --
> two heads, which breaks `flask db upgrade`. This chain now starts at
> `008_add_user_identity` with `down_revision = '007_regatta_catalog'`.
> Verified: `flask db heads` reports a single head.

Follow `migrations/versions/006_add_regatta_checks.py`'s style: explicit
`revision`/`down_revision` strings, `server_default` on new non-null columns (existing rows
need a value), and batch-mode-safe ops so local SQLite still migrates.

---

## Sequencing recommendation

- Phases **0 → 1 → 2** are the foundation and should land together.
- **Phase 2's backfill is the one irreversible-feeling step** — dry-run it against a Neon
  branch before prod.
- Then **5a + 5b** (schema plumbing) before **4**, because manual results need the
  qualified-ID scheme to be selectable on a resume at all. Building Phase 4 first means
  writing the UI twice.
- **Phase 3's** tier-2 verification can ship any time after Phase 1, and should ship before
  resumes become publicly shareable by claimed HS/College users.

**Rough shape:** Phase 0 is an afternoon. Phases 1+2 are the real work (new table, backfill,
rewriting every ownership check). Phase 5c's 8-template edit is the most tedious and least
risky part.

---

## What is still open

Both original decisions are settled (see the DECISION TAKEN notes in Phases 3
and 4). What remains:

1. **Migration 012** — drop `users.sailor_id` once 008-011 are stable in prod,
   and drop the mirroring writes at the same time (see the migration table).
2. **CSRF protection** — the app has never initialized `CSRFProtect`, so the new
   POST routes (claim, release, manual-result delete) rely on session cookies
   alone, exactly like every pre-existing POST. Turning it on app-wide would
   break the JSON `fetch` calls in the resume builder and the admin scraper
   controls unless those are given tokens too. Worth doing as its own change.
3. **`001_initial.py` cannot run on SQLite** (`op.create_foreign_key`, line 119),
   so the migration chain has only ever run against Postgres. Migrations 008-011
   are batch-mode-safe and were tested up/down/up on SQLite, but a from-scratch
   local setup still needs `flask init-db` rather than `flask db upgrade`.
4. **Manual-result `verified` flag** has no admin UI — the column is written only
   by the sailor's own edits, which reset it to false.
5. **First admin must be granted from the CLI**: `flask make-admin <email>`.
   `is_admin` defaults false for every existing account, so until that runs,
   nobody can reach `/admin` or the scraper controls.
