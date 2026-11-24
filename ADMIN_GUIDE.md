# SkillOps: supervisor guide

Install, deploy, AWS costs and teardown are in [README.md](README.md). This guide is about using SkillOps.

SkillOps has two sides for you:

- **Monitoring** — the menu on the left (Dashboard, Batches, Courses, Runbooks, Live Data Simulator, MCQ tests,
  Audit log, Health). These pages only show; nothing on them changes anything. The ☰ button at the top left hides
  or shows the menu (on a phone it slides in over the page).
- **Settings** — open the profile menu (your initial, top right) → **Settings**. Every change is made here:
  institutions and programmes, batches (trainers, class days, current project, marking a project complete,
  runbook versions, and the batch's Live Data Simulator and MCQ tests), trainers, courses, runbooks, simulator
  data, MCQ connections, import, export and the Dashboard tabs. A gold bar across the top lists the Settings pages.

Who a screen is for shows at a glance: your top bar is **navy** with an **Admin** badge that says *Monitoring* or
*Settings* (click it to switch; Settings also has a gold line under the bar). Trainers see a white bar with a
**teal** stripe and a **Trainer** badge; students see a **plum** stripe and a **Student** badge.

Trainers have no side menu: they open into their first batch and switch batches from the **Batches** bar under
the title.

The rest of this guide says "batch settings" for Settings → Batches → the batch.

## 1. Courses, institutions, programmes, batches, trainers

**Robomy** owns the **courses**; each course holds **projects** in teaching order, and each project uses one
runbook. **Partner institutions** have **programmes**; a programme selects courses and so gets their
projects. Each programme has **batches**. **Trainers** are a list of their own: add a trainer to the
institutions they work with, then link them to those institutions' batches.

1. **Courses → Add a course** (for example Data Analytics). Open it and add its **projects** in teaching order
   (↑ ↓ to reorder). Choose each project's **runbook** in its row once a runbook is published. Project names are
   unique across courses. A project added to a course reaches every programme that uses the course.
2. **Institutions → Add an institution** (name, short code). Optional logo (PNG/JPEG under 300 KB): shown in the
   top bar on that institution's pages.
3. Open the institution → **New programme**. Open the programme:
   - **Courses**: tick the courses and press **Save courses**; their projects become the programme's projects
     (courses in the order shown, ↑ ↓ to change it; projects in each course's order).
   - **Projects**: **Take out** removes one project from this programme only (its ticks are kept; it is listed
     under "Taken out" with **Put back**). **Add a project from another course** adds a single project of a
     course the programme does not take, at the end.
   - **Batches** (name, and the MCQ batch name = the `Batch` column in the MCQ Students sheet).
4. **Trainers**: on the institution's page, **Add a trainer from the list** or **Or add a new trainer** (name,
   email). The **Trainers** page lists everyone with their institutions and batches (filter by institution at
   the top); a trainer can be added there too, and on a trainer's page you add or remove their institutions and
   tick their batches. Cognito emails each new trainer a temporary password; send them the SkillOps address
   yourself. A batch's settings can link only trainers of its own institution; taking a trainer off an
   institution unlinks them from its batches (their ticks are kept).
5. A trainer sees only the batches they are linked to.
6. **Archive** (institution, programme or batch) hides it from trainers and keeps everything. With **Show
   archived**, archived rows are filled red with an **ARCHIVED** badge, and an archived item's own page has
   a red banner. Turned-off trainers are shown the same way. **Turn off account** signs a trainer out at once.
7. **Delete** (institution, programme, batch, trainer, course) is under **Archive** on each page; type the
   name to confirm. A project is deleted from its row's ⋯ menu on its course's page. Each removes the thing
   with everything in it: a batch takes its ticks, notes, session plans, trainer links and Live Data Simulator
   students; a programme its batches; an institution its programmes, its trainer list, MCQ connection and logo
   (the trainers stay, with their other institutions); a course its projects (every programme loses them); a
   trainer their sign-in (their past ticks keep their name). Runbooks stay in the library. Archive instead
   when you may want the history.
8. **Class days** (batch settings → **Class days**): the weekdays the batch meets (for example Mon, Tue, Thu),
   the first and last day, days off and extra days. Trainers see them on the batch's **Calendar** and plan a
   session for each one; you see which days have a plan.
9. **Current project** (the batch's **Progress** tab, under **Where the class is**): choose which project the
   batch is on now, or let it follow the latest tick. Progress shows that project step by step.

**First set-up from one file:** Import from CSV. Columns: `institution, institution_code, programme, course,
project, batch, mcq_batch, trainer_name, trainer_email` (download the template on the page). A row with a
course and project adds them to Robomy's catalogue; a row with an institution, programme and course selects
that course for the programme; programme + project (no course) adds a project of another course; a trainer row
adds the trainer to the institution and links the batch. Check the file,
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

Click a batch to open it on its **Progress** tab, exactly as the trainer sees it, on one screen: five figures
(programme, current project, items marked done in the last 7 days, needs a repeat, gaps), every project's
completion, the current project's steps (open one for its parts), a chart of items marked done per day over 4
weeks, and folded lists of recent activity, items needing a repeat, gaps and trainer notes. It is **view only**: marking items, notes and
repeat flags are the trainers' work, and you can read session plans but not change them. **CSV** downloads
each view. **Team progress** (`/team`) is the trainers' side-by-side view of their programmes.

## 3. Runbooks

1. **Runbooks → Add a runbook** (for example "Airtel"). A runbook belongs to Robomy's shared library, so
   programmes at any institution can use it; each batch keeps its own ticks.
2. **Upload** the Word .docx. It is read in the background (refresh after a few seconds).
3. **Confirm the structure** (step 2 of 4, shown at the top). SkillOps says what it found, for example
   "10 Steps · 34 Parts · 418 Actions · 105 Sub-steps", and how (from the document's headings and bands, or
   laid out like a template you saved). If that is right, press **Looks right — check the tree**: the layout is
   also remembered as a template, so the next upload drawn the same way is laid out on its own.
   To change it, each pattern is drawn the way it looks in Word (a coloured band, a dark command strip, a
   heading) with its count and examples; pick **Text** or a level (1 · Step … 4 · Sub-step) for each. The bar
   at the top recounts as you choose. Patterns read as text are folded away; open them to make one a heading.
   Level names and the level trainers tick are below; **Remember this layout** is ticked already.
4. **Check the tree** (step 3): counts per level and the tree. Click an item to **rename** it, **merge** it into
   the one before, or move its **start one block earlier/later**. **Undo all edits** is there too.
5. **Publish** (step 4). Then on the course's page (Courses) choose this runbook for the project.
6. **Attach the runbook as a PDF** (optional, recommended). In Word: File > Save As > PDF, keep *Create bookmarks
   using Headings* on, then on the runbook's page press **Attach PDF** on that version. When a trainer downloads
   a session plan as PDF, each planned part is cut straight out of this PDF, so it keeps the runbook's own look.
   The version row says how many pages were found in the PDF ("by its bookmarks" or "by their headings"). A part
   that was not found, or a version with no PDF, is laid out from the runbook's pages instead, so a plan is never
   missing anything. Attach the PDF again after you change the Word file; each version has its own.
   With the PDF attached, **View** (runbook's page) and **Monitoring > Runbooks** open the version as the original
   PDF in a large window, with the sections on the left; trainers' **View runbook** and every link to a part of the
   runbook open it the same way. Without a PDF they still open the runbook's pages.

**Delete an upload:** on the runbook's page, the × on a version deletes that upload (its file, pages and
images) when no batch is on it and no batch has ticked it. **Delete this runbook** (type its name to confirm)
removes the runbook and all its uploads when no project uses it. Versions with ticks always stay: they are
the batches' history.

**New version:** upload the new .docx on the runbook's page. The previous mapping is used if it still fits.
After publishing, the versions table shows which items of the old version could not be matched (matched by
action ID such as `2.4` first, then by title). Each batch stays on its version until you press **Move** (on
the runbook's page or the batch's settings); matching ticks and notes are carried across, and the old
ticks are kept.

## 4. Live Data Simulator

**Add data from the page.** **Live Data Simulator → Add data**: upload a SQLite `.db` or a `.csv` (up to
400 MB). SkillOps reads it and lists every table with its rows, columns and the date ranges it found. For each
table tick it, give the dataset name, and say when a row appears for students: **at its time** (a time
column), **at the start / end of its date**, **end of its date + hour**, or **all at once** (a reference
table). Choose an existing feed or name a new one (data start, go-live in India time, speed), tick **Show
to students** if they should see it at once, and press **Load**. The page shows how far it has got; the
result is in the audit log. Loading a dataset name that exists replaces its rows (its version goes up).

**Very large files** (millions of rows) load faster from your laptop with `admin.py`:

```bat
python admin.py inspect --db uber_live.db
python admin.py upload --db uber_live.db --mapping datasets.json --feed uber --go-live "2026-11-16 09:00" --tz Asia/Kolkata
python admin.py activate --dataset uber_trips_live uber_riders_live
python admin.py clock --feed uber
python admin.py pause --feed uber            (and resume)
python admin.py set-clock --feed uber --speed 2 --from-now
python admin.py replace --db uber_live_v2.db --mapping datasets.json --table trips
```

The **Live Data Simulator** page shows each feed (running, paused or not started), its data time, go-live
and speed, and each dataset (rows, released now). Pause, resume, change the clock and activate datasets
there too. Join codes, students and activity are on each batch's Live Data Simulator tab.

**Delete data:** the × on a dataset row deletes it with all its rows; **Delete this feed** (type its name)
deletes the feed with all its datasets. Rows are removed in the background.

Students use two public pages, in the same look as SkillOps (staff who are signed in are sent back from Register): **Register** (`/register`, with the join code;
the API key is shown once) and **Developer docs** (`/docs`, with a "Try it" box). The old landing and status
pages are gone: `/live` opens Register, `/status` opens the docs (the `/v1/status` API still answers).

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
