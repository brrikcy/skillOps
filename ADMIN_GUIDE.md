# SkillOps: supervisor guide

Install, deploy, AWS costs and teardown are in [README.md](README.md). This guide is about using SkillOps.

## 1. Institutions, programmes, batches, trainers

The structure is **institution → programme → batches**, and **trainers** are linked to batches.

1. **Institutions → Add an institution** (name, short code). Optional logo (PNG/JPEG under 300 KB): shown
   in the top bar on that institution's pages.
2. Open the institution → **Add programme**. Open the programme:
   - **Projects** in teaching order (↑ ↓ to reorder). Each project uses one runbook (chosen in the
     project's row once a runbook is published).
   - **Batches** (name, and the MCQ batch name = the `Batch` column in the MCQ Students sheet).
3. **Trainers → Add a trainer** (name, email). Cognito emails a temporary password; send them the
   SkillOps address yourself. Link batches on the trainer's page (tick boxes, across institutions) or
   on a batch's settings.
4. A trainer sees only the institutions, programmes and batches they are linked to.
5. **Archive** (institution, programme or batch) hides it from trainers and keeps everything.
   Nothing is ever deleted from the app. **Turn off account** signs a trainer out at once.

**First set-up from one file:** Import from CSV. Columns: `institution, institution_code, programme,
project, batch, mcq_batch, trainer_name, trainer_email` (download the template on the page). Check the file,
read the list of changes, then **Apply**. The same file twice changes nothing. From your laptop:
`python admin.py import-csv --file setup.csv`.

## 2. The progress dashboard

Your home page. Choose an institution and a programme (it opens on the most recently active one).
One row per batch: trainers, current project, position (the furthest ticked item, for example
"Step 2 · Part 3 · 2.9"), project completion with a strip of one cell per step, projects finished, when and
by whom the last item was ticked, live-data registrations and fetches, MCQ tests taken and the latest
average. Every number is counted from the stored ticks. There are no due dates or "behind" colours on
purpose: batches move at different speeds.

Above the table: **Milestones** (steps and projects each batch finished in the last 14 days) and
**Trainer activity** (items each trainer marked done in the last 7 days, and when they were last active).
These recognise delivery; they never rank trainers. A batch row also shows how many items its trainers
flagged **to revisit**.

Click a batch for its detail: every project's completion, the current project's steps and parts, recent
ticks, open items before the furthest tick ("gaps"), items flagged "needs a repeat", and trainer notes. **Open batch** shows the runbook
exactly as the trainer sees it; you can tick too. **CSV** downloads each view.

## 3. Runbooks

1. **Runbooks → Add a runbook** (for example "Airtel"). A runbook belongs to Robomy's shared library, so
   programmes at any institution can use it; each batch keeps its own ticks.
2. **Upload** the Word .docx. It is read in the background (refresh after a few seconds).
3. **Map**: the detector lists every heading-like pattern with its count and three examples: heading
   styles, coloured one-row bands (`PART n`, action words like SAY/DO with an ID like `2.4`, short markers
   like `1`, `A`, `OK`), command strips, decision chips, bold labels. Choose Level 1–4 or "Content" for each.
   A suggestion is filled in (command strips and decision chips are content). Name the levels, choose the
   level trainers tick, and tick **Save as a template** (for example "Robomy runbook v3"). The next runbook
   drawn the same way is offered that template with a match score, and maps in one click.
4. **Preview**: the tree with counts per level. Click an item to **rename** it, **merge** it into the one before,
   or move its **start one block earlier/later**. **Undo all edits** is there too.
5. **Publish**. Then on the programme's page choose this runbook for the project.

**New version:** upload the new .docx on the runbook's page. The previous mapping is used if it still fits.
After publishing, the versions table shows which items of the old version could not be matched (matched by
action ID such as `2.4` first, then by title). Each batch stays on its version until you press **Move** (on
the runbook's page or the batch's settings); matching ticks and notes are carried across, and the old
ticks are kept.

## 4. SkillOps Live Data

Load feeds from your laptop with `admin.py` (they can be large):

```bat
python admin.py inspect --db uber_live.db
python admin.py upload --db uber_live.db --mapping datasets.json --feed uber --go-live "2026-11-16 09:00" --tz Asia/Kolkata
python admin.py activate --dataset uber_trips_live uber_riders_live
python admin.py clock --feed uber
python admin.py pause --feed uber            (and resume)
python admin.py set-clock --feed uber --speed 2 --from-now
python admin.py replace --db uber_live_v2.db --mapping datasets.json --table trips
```

The **Live data** page shows each feed (running, paused or not started), its data time, go-live and speed,
and each dataset (rows, released now). Pause, resume, change the clock and activate datasets there too.
Join codes, students and activity are on each batch's Live data tab. The mapping file and release rules
are the same as in ROBOMY DSE (see the Developer docs page for students).

## 5. MCQ tests

The one-time set-up for each institution (add `Api.gs`, run `setupApi`, deploy a new version, paste the
`/exec` link and the secret, Test connection, set each batch's MCQ batch name) is in README section 9.
**Re-mark a test** (after fixing an answer in the Questions sheet) is on the MCQ tests page.

## 6. Export and restore

**Export → Start a new export → Download** (after a few seconds). The .zip holds CSV files for Excel and
JSON files for a full restore. Keep a copy somewhere safe each week.

Restore into a stack (for example a new one):

```bat
python admin.py restore --file skillops-export-job-xxxx.zip
```

Existing items are kept; `--overwrite` replaces them. The restore also makes each trainer's sign-in
account again (Cognito emails a temporary password) and publishes every runbook version again from the
Word files inside the export. Not in the export: sign-in sessions, MCQ secrets (paste them again on the
MCQ tests page) and live-data rows (upload the .db files again). If a trainer cannot sign in after a
restore, press **Send the invitation again** on their page, or run `python admin.py sync-accounts`. For mistakes in the last 35 days, use DynamoDB point-in-time
recovery (README section 8).

## 7. Audit log and health

**Audit log:** every change and every sign-in: who, role, action, batch, target, result, request ID. Filter
by date, person, action, batch or result; download CSV. **Health:** checks each table, both buckets,
Cognito and each institution's MCQ system, with the time each took.
