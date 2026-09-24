Linear: TEC-946

# A profile photo has to show the member Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member cannot present themselves to the community with a picture that is not of a person, and when a photo is taken down they are told why and what to do.

**Architecture:** A face check runs in the same cron that already scores photos for nudity, writing to the same `moderation_status` column that every read path already gates on. Nothing new is invented: no new table, no new hiding mechanism, no new admin screen. The one genuinely new thing is an email, because taking a photo down silently is the current behaviour and it is the wrong behaviour.

**Tech Stack:** Python, `onnxruntime` and `numpy` and `Pillow` (all already in `requirements.txt`), plus `opencv-python-headless` for the YuNet face detector. Flask API repo `ahavah-api`.

**Spec:** none. This is a product gap found by audit on 2026-09-20, not a defect against an existing spec.

## What we found, which is the reason for this plan

Every member's primary photo was reviewed by eye on 2026-09-20 (50 accounts with photos).

| Member | id | Primary photo | Account |
|---|---|---|---|
| Max | 113 | abstract hexagon wallpaper, no person | active, 1 photo |
| Navon | 117 | AI bear head on a suited body, and his second photo is the same | active, joined 19 Sep |
| Vict | 49 | a German Shepherd | never activated, last seen 5 Jul |
| Abby | 31 | a real photo of her, shot from behind, no face | active, 1 photo |
| Raphael | 105 | a group of four, but his photo 2 is a real solo portrait | active |
| Jan Perkins | 103 | a couple, 4 photos | never activated |

The Admin account (23) uses the logo and is correct.

**Why nothing caught it.** The only content check that runs is the nudity classifier, a 5 part 210 MB ONNX model derived from Bumble's private-detector, scored by `service/cron/nsfwphotorunner`. It answers one question, "is this lewd", and it answered correctly: Max scored 0.052 and Navon 0.065. No code in any of the three repos performs face detection. Upload itself checks only format, size, dimensions and an exact MD5 against `banned_photo_hash`.

## What this plan does not do

- **It does not prove the photo is of that specific member.** That is a different and much larger piece of work, and it is impossible today: the verification selfie is hard deleted after three days (`service/cron/garbagerecords`) and only an MD5 survives, so no reference image or biometric template exists to compare against. A staged path to it is sketched at the bottom, out of scope here.
- **It does not add an admin screen.** `ahavah-admin` already has a working Photos moderation tab wired to live approve and reject routes.
- **It does not change the nudity model, its thresholds, or any existing moderation verdict.**

## Global Constraints

- Pushing `ahavah/main` deploys production. Work on `photo-face-check`; deploy only after review and on the owner's go.
- **A false reject is worse than a false accept.** Wrongly hiding a real person's face tells a real member they look fake. Every threshold in this plan is tuned to let borderline images through to a human, never to auto reject them.
- No member is ever left without an explanation. A photo that moves to `rejected` sends the note in Task 4, or it does not move.
- No em dashes on added lines. Sentence case. No attribution trailers in any commit.
- Never nest `api_tx`. No literal `%` in psycopg SQL.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 809).

---

### Task 1: A face detector the cron can run

**Files:** `requirements.txt`, `antiabuse/facecheck/__init__.py` (new), `antiabuse/facecheck/README.md` (new), `cron.Dockerfile`, `tests/test_face_check.py` (new), test fixture images.

- [ ] Add `opencv-python-headless` to `requirements.txt`. It carries the YuNet detector, a 230 KB ONNX model, which is the cheapest accurate option and needs no new inference runtime. The cron image already ships a 210 MB model, so the size is precedented.
- [ ] `detect_faces(image_bytes) -> list[FaceBox]` returns zero or more boxes with a confidence and the box area as a fraction of the image. Pure function over bytes, no network, no database.
- [ ] It must never raise. A corrupt, truncated, CMYK, animated or enormous image returns `[]` with a log line, exactly as `is_banned_photo` degrades today. Feed it garbage in a test and assert it returns cleanly.
- [ ] Fixtures: a clear single face, a face in profile, two faces, a very small face in a wide landscape shot, a dog, a flat colour, and a synthetic abstract pattern. **Use the real images from the audit where the member has already been asked, and synthetic or public-domain images otherwise. Do not commit a member photo to the repo.** Store fixtures under `tests/fixtures/facecheck/` and note their provenance in the README.
- [ ] Tests assert counts, not pixel values: the dog and the abstract pattern give zero faces, the portraits give one.

### Task 2: The cron records what it saw

**Files:** `migrations/0053_photo_face_check.sql` (new), `service/cron/nsfwphotorunner/__init__.py`, `service/cron/nsfwphotorunner/sql/__init__.py`, tests.

- [ ] Migration adds two nullable columns to `photo`: `face_count INT` and `face_checked_at TIMESTAMP`. Nullable because every existing row is unchecked, and a backfill is Task 5.
- [ ] The existing runner already downloads the 450px render for every photo with a NULL `nsfw_score`. Run the face check on those same bytes, in the same pass, and write both results. **Read the runner before changing it** and keep the nudity path untouched: a face check failure must not stop a nudity score from being written.
- [ ] **Verdict rules, and these are deliberately timid:**
  - `face_count >= 1` — no change to the existing nudity verdict. The photo is fine as far as this plan is concerned.
  - `face_count == 0` — set `moderation_status = 'manual_review'`, never `'rejected'`. A human decides. This is the whole safety margin: the detector is good, not perfect, and a member whose face is small, masked, in heavy shadow or turned away must not be auto hidden.
- [ ] A photo already `rejected` by the nudity rule stays rejected. The stricter verdict always wins.
- [ ] Tests: a face photo keeps its status; a no-face photo lands in `manual_review`; a photo that is nudity rejected stays rejected even with a face; a detector exception still lets the nudity score be written.

### Task 3: The operator sees why it was flagged

**Files:** `service/api/admin/moderation_routes.py`, `ahavah-admin/src/components/admin/tab-moderation.tsx`, `ahavah-admin/src/lib/queries.ts`, tests.

- [ ] The photos moderation payload gains `face_count` and `face_checked_at`.
- [ ] The Photos tab shows a plain reason beside each queued photo: "No face detected" when `face_count` is 0, alongside the existing NSFW score band. No new screen, no new layout, one field.
- [ ] Tests: the route returns the new fields; a photo with a face shows no reason chip.

### Task 4: A takedown tells the member

**Files:** `emails/photo_removed.py` (new), `service/api/admin/moderation_routes.py`, tests.

- [ ] Today `POST /admin/moderation/photos/<uuid>/reject` sets the status, writes an audit row and tells the member nothing. Their photo simply stops appearing. Fix that.
- [ ] The note is built on `emails/member_note.py`, which exists for exactly this and is already on the canonical shell. **Do not hand roll a template and do not invent a new title image**: use the existing `title-note.png` and `title-note-wht.png` pair. See `tests/test_email_templates_use_the_brand_shell.py`, which fails CI if this is ignored.
- [ ] Copy says three things and nothing else: what happened, what is needed, and that they are welcome to stay. It never calls the member a liar and never uses the word fake. Draft in Task 4's brief, to be approved by the owner before the first send.
- [ ] The reject route takes an optional `reason` and a `notify` flag defaulting to true. **The email failing must never roll back the takedown**: send after the status is committed, catch everything, log, and report the send result separately in the response so the operator knows whether it went.
- [ ] Tests: a reject with `notify` true queues exactly one note to the right address; a suppressed domain sends nothing and still rejects; a raising mailer still leaves the photo rejected; `notify` false sends nothing.

### Task 5: Backfill, and the members already affected

**Files:** `scripts/backfill_face_check.py` (new), runbook notes.

- [ ] A one-shot script clears `nsfw_score` to NULL in batches so the existing runner re-processes historical photos, or calls the checker directly, whichever the Task 2 author finds cleaner. It must be resumable and must not re-download a photo it has already checked.
- [ ] Run it against production only after Tasks 1 to 4 are deployed, so anything it flags lands in `manual_review` with a working notify path behind it.
- [ ] The six accounts in the audit table are handled by hand by the owner through the Photos tab, not by this script. Max and Navon are the two clear cases.

### Task 6: Review and deploy

- [ ] Whole branch review; one fix wave if needed.
- [ ] Deploy the API. Watch the first cron pass, confirm no existing photo flips to `rejected`, and confirm the counts of `manual_review` look sane rather than enormous.
- [ ] **If more than about 15 percent of existing photos land in `manual_review`, stop and retune before notifying anybody.** That ratio means the detector is wrong, not the members.

## Later, and only if the owner wants it: proving it is actually them

Out of scope, recorded so the staging is deliberate.

1. **Same person across their own photos.** Compute a face embedding per photo and compare within the account. Catches a member mixing a stock portrait in with real ones. Needs an embedding model, no schema beyond a vector column.
2. **Same person as the verification selfie.** Today impossible: the selfie is deleted after three days and only an MD5 is kept. It becomes possible if verification stores a face *embedding* rather than the image, which is both smaller and more private than retaining a photo. That would also let the verification badges mean what they say, which is the gap found on 2026-09-20 when 13 members held a gold tier with no evidence behind it.

Both are separate plans. Neither is needed to stop a wallpaper being a profile photo.

## Self-review record

- The riskiest thing here is a false reject, so nothing in this plan auto rejects. The worst a false negative can do is put a real member's photo in front of a human, which costs the owner a click.
- Reuse over invention throughout: the existing cron, the existing `moderation_status`, the existing read-path gating, the existing admin tab, the existing note template. The only new surface is one email, and that is fixing a silent action rather than adding a feature.
- Task 4 is the task that makes Task 2 safe to turn on. They ship together or not at all.
