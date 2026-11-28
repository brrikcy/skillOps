# AWS guide: how SkillOps uses AWS, install, deploy, back up

Moved from the main README. Section numbers are kept. Back to the [README](../README.md).

## 1. How this app uses AWS

```
 Browser (supervisor, trainers)
        |  HTTPS
        v
 +--------------+   /static/*  -> S3 web bucket (CSS, JS, logo)
 |  CloudFront  |   /media/*   -> S3 runbook bucket, media/ only (images, logos)
 +------+-------+   everything else
        v
 +--------------+      +--------------------+      +--------------------------+
 | API Gateway  | ---> | Lambda: WebFunction| ---> | DynamoDB: Core, Progress,|
 | (HTTP API)   |      | (makes each page)  |      | Audit, Live* (PITR on)   |
 |              |      +----+---------+-----+      +--------------------------+
 |   /v1/* ------------> Lambda: StudentApi (live data, students' Python code)
 +--------------+           |         |  async
                            |         v
                            |   Lambda: JobsFunction (read/publish runbooks, export) -> S3 runbook bucket
                            v
             Cognito (passwords)   Secrets Manager (secrets)   Apps Script web app (MCQ tests, Google)
 CloudWatch: logs, dashboard, alarms -> SNS email.   Budgets: monthly cost email.
```

Prices are for Mumbai (`ap-south-1`) in US dollars, rounded. Check the AWS pricing pages for today's numbers.

**CloudFront.** What it is: a content delivery network. It answers browsers over HTTPS from places near
them, including India. Here: it is the one address of SkillOps. It serves the CSS, JavaScript and images
from S3 (cached), and passes every page request to API Gateway without caching, with cookies and the
query string. A tiny CloudFront Function copies the browser's address into a header, so the app knows
its own address. Cost: the always-free tier (1 TB and 10 million requests a month) covers us.

**API Gateway (HTTP API).** What it is: a managed front door for web requests. Here: it receives each page
request from CloudFront and hands it to the WebFunction Lambda. It also has a safety throttle
(100 requests a second). Cost: about $1.00 per million requests; nothing when idle. The first
million requests a month are free for your first 12 months.

**Lambda.** What it is: run code without a server; you pay only while it runs. Here: `WebFunction`
makes every page; `StudentApiFunction` answers students' Python code for live data (`/v1/...`);
`JobsFunction` does long work in the background (reading and publishing a runbook takes 30–90
seconds; an export); `WebPublisherFunction` runs only during a deploy or delete. Cost: $0.20 per
million requests plus a tiny charge per millisecond. The always-free tier (1 million requests and
400,000 GB-seconds a month) covers us many times over.

**DynamoDB (on-demand).** What it is: a fully managed database; on-demand means you pay per read and
write, with no servers. Here: `Core` holds institutions, programmes, projects, batches, trainers,
their links, runbooks, versions, templates, jobs and sign-in sessions. `Progress` holds ticks and
notes (per batch, runbook version and item). `Audit` holds the audit log. Five `Live*` tables hold
Live Data Simulator (rows, feeds and join codes, students, fetch log, rate limits).
**Point-in-time recovery** is on for every table: AWS keeps 35 days of history, and you can restore a
table to any second in that time. Cost: roughly $0.70 per million writes and $0.15 per million reads;
storage is free for the first 25 GB; point-in-time recovery is $0.20 per GB-month (a few MB,
plus the live-data rows you load: a 1.2-million-row table is about 0.4 GB, so about $0.08 a month).

**S3.** What it is: file storage. Here: a **web bucket** (CSS, JavaScript, logo) and a **runbook
bucket** (uploaded runbooks, their pages and images, institution logos, exports). The runbook bucket
keeps old versions of every file for a year, so nothing is lost by mistake. Exports are removed
after 30 days. Both buckets are private: CloudFront may read only `static/` and `media/`. Cost: a few paise.

**Cognito.** What it is: user accounts and passwords as a service. Here: it holds your account and the
trainers' accounts, emails temporary passwords and "forgot password" codes, and checks passwords.
SkillOps shows its own sign-in page and asks Cognito on the server. Students do not use Cognito.
Cost: free at this size (the first 10,000 monthly users).

**Secrets Manager.** What it is: a safe place for secrets. Here: two secrets. `ServerSecret` (made by AWS
at deploy; used by Live Data Simulator from Phase 3) and `McqSecrets` (each institution's MCQ connection
secret, set from the app in Phase 4). Secrets are never in the code or in an export. Cost: $0.40 per
secret a month = $0.80.

**CloudWatch and SNS.** What they are: logs, graphs and alarms; SNS sends the alarm emails. Here: every
Lambda writes logs (kept 30 days). Every error shows the person a request ID; search the log for it.
Alarms email you when pages fail (5xx) or a Lambda or job has errors. Cost: inside the free tier.

**Budgets.** What it is: a cost watcher. Here: one monthly budget emails you at 80% of the amount you
choose, and when AWS forecasts you will pass 100%. Cost: free.

**CloudFormation and SAM.** CloudFormation builds and deletes everything above from `template.yaml`, as one
"stack". SAM (Serverless Application Model) is a shorter way to write the Lambda parts, plus the
`sam` command. Cost: free.

No server runs all the time. There is no VPC and no NAT gateway, so the idle cost stays near zero.

## 2. Install on Windows

You need:

1. **Python 3.12 or 3.13** (https://www.python.org/downloads/, tick "Add python.exe to PATH").
2. **AWS CLI v2** and **AWS SAM CLI**. In Command Prompt:

```bat
winget install --id Amazon.AWSCLI -e
winget install --id Amazon.SAM-CLI -e
```

3. **Docker Desktop** (already on this laptop). `sam build --use-container` builds inside Docker, so the
   Lambda packages are made for Linux with Python 3.12 exactly as in AWS.

Close and open Command Prompt, then check:

```bat
python --version
aws --version
sam --version
docker --version
```

macOS / Linux: install the same tools (`brew install awscli aws-sam-cli`), then `python3 --version` etc.

Set up the project (Command Prompt, inside this folder):

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt -c requirements-dev.lock
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -c requirements-dev.lock
```

Every package has an exact version (`src/requirements.txt` for AWS, `requirements-dev.lock` for your
laptop), so the app builds the same way every time.

## 3. AWS credentials

`sam` and `admin.py` use **your** credentials. Trainers never need any.

1. Sign in to the AWS console as the account owner. Turn on MFA for the root user.
2. **IAM → Users → Create user**, for example `robomy-admin`. Attach the policy `AdministratorAccess`
   (simplest while learning). Open the user → **Security credentials → Create access key** →
   "Command Line Interface".
3. In Command Prompt:

```bat
aws configure
```

Type the access key, the secret key, region `ap-south-1`, output `json`. Check:

```bat
aws sts get-caller-identity
```

(Better practice later: IAM Identity Center and `aws configure sso` instead of long-lived keys.)

## 4. Try it on your laptop first (no AWS)

```bat
python -m pytest
python demo.py
python local_server.py --seed
```

macOS / Linux: the same with `python3`.

- `pytest` runs the automatic tests against **moto**, a fake AWS in memory.
- `demo.py` creates two institutions, batches A, B and C with a trainer each, and shows who sees what.
- `local_server.py --seed` serves the app at http://127.0.0.1:8790 with sample data. The sign-in page
  shows "Local mode: sign in as" buttons (there is no Cognito on the laptop). This shortcut exists only
  in local mode; in AWS it is switched off.

## 5. Deploy, update, delete

> **No AWS sign-in on your own laptop?** Deploy from GitHub instead: see [DEPLOY_WITH_GITHUB.md](DEPLOY_WITH_GITHUB.md). An AWS
> administrator creates one role once ([AWS_ADMIN_NOTE.md](AWS_ADMIN_NOTE.md)); after that a button in GitHub deploys and updates.
> The commands below are the manual way and give the same result.

### First deploy

```bat
sam build --use-container
sam deploy --guided
```

Suggested answers to `sam deploy --guided`:

| Question | Answer |
|---|---|
| Stack Name | `skillops` (admin.py uses this name by default) |
| AWS Region | `ap-south-1` |
| PlatformName / LiveDataName | `SkillOps` / `Live Data Simulator` |
| SuperadminEmail | your email |
| SuperadminName | your name |
| AlarmEmail | leave empty (uses your email) |
| MonthlyBudgetUSD | for example `10` |
| SessionHours | `12` |
| WebDomainName, WebCertificateArn | leave empty for now |
| Confirm changes before deploy | `y` |
| Allow SAM CLI IAM role creation | `y` |
| Disable rollback | `n` |
| WebFunction has no authentication. Is this okay? | `y` (the app checks the sign-in on every page itself) |
| Save arguments to configuration file | `y` (creates `samconfig.toml`) |

The first deploy takes 5–10 minutes (CloudFront is the slow part). At the end SAM prints **Outputs**;
`WebUrl` is the SkillOps address.

Then:

1. Open your email and **confirm the SNS subscription** ("AWS Notification - Subscription Confirmation"),
   or alarm emails will not arrive.
2. You also get "Your SkillOps account" with a temporary password.

### Update after changing code

```bat
sam build --use-container
sam deploy
```

### Delete everything

```bat
sam delete --stack-name skillops --region ap-south-1
```

This removes every table, function, both buckets (emptied first, every file version), the user pool,
alarms, dashboard, budget and log groups. **All data is deleted.** Export first (section 8).

## 6. First sign-in

1. Open `WebUrl`. Sign in with your email and the temporary password from the email.
2. Choose your own password (at least 10 characters with a capital letter, a small letter and a number).
3. You are the **Supervisor**. Add Robomy's **Courses** and their projects (left menu, under Robomy), then go to
   **Institutions** (under Partners) and add the first
   institution, or **Import from CSV** to set everything up from one file.

Adding a trainer emails them a temporary password. Send them the `WebUrl` address yourself
(the email cannot contain it unless you use your own domain).

## 7. Custom domain (optional)

1. In the AWS console switch to **US East (N. Virginia)** → Certificate Manager → Request certificate →
   for example `skillops.robomy.in`, DNS validation. Add the CNAME record it shows at your DNS provider.
   Wait until "Issued".
2. `sam deploy --parameter-overrides WebDomainName=skillops.robomy.in WebCertificateArn=arn:aws:acm:us-east-1:...`
3. At your DNS provider add a CNAME: `skillops` → the `CloudFrontDomain` output.

## 8. Export and restore

- **Export** (left menu, under System) **→ Start a new export.** Refresh the page; when it says "done",
  download the .zip. It has CSV files (for Excel) and JSON files (for a full restore).
- From your laptop instead: `python admin.py export --out skillops-backup.zip`
- **Restore** into a stack (for example a new one after `sam delete`):

```bat
python admin.py restore --file skillops-backup.zip
```

  Existing items are kept; add `--overwrite` to replace them. Cognito accounts are not in the export:
  `admin.py restore` makes each trainer's sign-in account again (a temporary password is emailed),
  publishes the runbooks again from the Word files in the export, and tells you to paste the MCQ secrets again.
- **Undo a mistake from the last 35 days:** DynamoDB point-in-time recovery. Console → DynamoDB →
  Tables → the table → Backups → Restore to point in time. This makes a new table; ask before switching to it.

## 9. Runbooks, live data and MCQ tests (short version; details in ADMIN_GUIDE.md)

**Runbooks.** Runbooks → Add a runbook → upload the Word .docx. In AWS the file goes straight from your
browser to S3 (up to 40 MB) and is read in the background. Then **Map** (choose a level for each pattern
the detector found; save it as a template), **Preview** (rename, merge, move a start), **Publish**. On the
course's page (Courses) choose the runbook for a project. A new upload is a new version: batches stay on their
version until you press **Move** on the batch's settings (matching ticks are carried across).

**Live Data Simulator.** Load a .db file from your laptop (large files never go through a web page):

```bat
python admin.py inspect --db uber_live.db
python admin.py upload --db uber_live.db --mapping datasets.json --feed uber --go-live "2026-11-16 09:00"
python admin.py activate --dataset uber_trips_live
```

Trainers make join codes on their batch's **Live Data Simulator** tab. Students register at `<WebUrl>/register`,
read `<WebUrl>/docs`, and use `SIMLAB_API_URL=<WebUrl>` in their code. Feeds and the clock (pause,
resume, go-live, speed) are under **Live Data Simulator** in the menu, with **Add data** to load a .db or .csv from the page.

**MCQ tests: one-time set-up for each institution.**

1. Open the institution's MCQ Google Sheet → **Extensions → Apps Script**.
2. **File → New → Script**, name it `Api`, paste the whole of `appsscript/Api.gs`, save. Do not change the other files.
3. Choose the function `setupApi` and press **Run** (allow the permissions it asks for).
4. Open **Executions** (or the log under the editor) and copy the secret it shows. It is shown only once.
5. **Deploy → Manage deployments →** edit (pencil) → **Version: New version → Deploy**. The `/exec` link stays the same.
6. In SkillOps: **MCQ tests →** the institution → paste the `/exec` link and the secret → **Save → Test connection**.
7. For each batch, set its **MCQ batch name** (the `Batch` column of the Students sheet) on the batch's settings.

Trainers then use the batch's **MCQ tests** tab: open/close a test, copy the student link, the live monitor
(add 5 minutes, end a student's test), results, marks register (CSV), import questions, allow a re-attempt.
The secret is kept in Secrets Manager, never in the database or an export.

**When Google's quota is reached.** Apps Script has limits: each run may last at most 6 minutes, about
30 runs may happen at the same time for one Google account, and there are daily limits (for example on
emails sent). Google's page "Quotas for Google Services" lists today's numbers for your account type.
When a limit is reached Google answers with a web page instead of data. SkillOps then shows "The test
system has reached a Google daily limit" on the MCQ panels and "not available" in the dashboard's MCQ
column; every other page keeps working. The limits reset after 24 hours. Students writing a test use the
Apps Script pages directly, so a SkillOps problem never stops a running test. The live monitor asks Google
at most once every 5 seconds per test however many trainers watch it.

## 10. Monitoring

- **Health page:** Health in the left menu (`/health`) checks each table, both buckets, Cognito and each institution's MCQ system.
- **Dashboard:** the `DashboardUrl` output (CloudWatch → Dashboards → `skillops`).
- **Logs:** CloudWatch → Log groups → `/skillops/web`, `/skillops/jobs`, `/skillops/student-api`. Every error page shows a
  request ID; search the log for it.
- **Alarms:** email you on page errors (5xx) and Lambda or job errors.
- **Audit log:** Audit log in the left menu. Every change and every sign-in, with filters and CSV download.

## 11. Cost

Assumptions: 10 trainers using the app all day, 5 days a week; a few thousand page views a day; 150 students
fetching live data every 5 minutes in class hours; one 1.2-million-row feed loaded (about $1, once).

| Item | While in use (per month) | Idle (per month) |
|---|---|---|
| CloudFront, API Gateway, Lambda | ~$0 (free tiers) | $0 |
| DynamoDB reads, writes, storage, PITR | ~$0.50–2 (live data with a class of 150) | ~$0.10 |
| S3 | ~$0.05 | ~$0.02 |
| Secrets Manager (2 secrets) | $0.80 | $0.80 |
| Cognito, SNS, Budgets, CloudWatch basics | $0 | $0 |
| **Total** | **about $2–4** | **about $1** |

**After your AWS credits end**, the same usage is charged to your card at these rates; nothing changes in
the app. The budget email warns you at 80% of your monthly amount. To stop all charges, delete the stack.

## 12. Teardown check

After `sam delete`, each of these should say the stack does not exist or print an empty list:

```bat
aws cloudformation describe-stacks --stack-name skillops --region ap-south-1
aws dynamodb list-tables --region ap-south-1
aws s3 ls
aws cognito-idp list-user-pools --max-results 10 --region ap-south-1
aws logs describe-log-groups --log-group-name-prefix /skillops --region ap-south-1
aws cloudfront list-distributions --query "DistributionList.Items[].Comment"
```

`sam delete` also offers to delete the `aws-sam-cli-managed-default` bucket that holds deploy packages;
say yes if you are finished. Secrets Manager keeps a deleted secret for 7–30 days (no charge).

## 13. Limits and notes

- Cognito's built-in email sends about **50 emails a day** (invitations, codes). Enough for staff.
  For more, connect Amazon SES to the user pool.
- Sign-in lasts `SessionHours` (12) after the last page visit. If it ends while you are saving a form,
  the form is kept for one hour: sign in again and press **Save it now**.
- Every form is safe to send twice (double click, retry): it is done once.
- Nothing is deleted from the app. Archive hides; history stays.
- Two-step sign-in (authenticator app) is not offered on the SkillOps sign-in page yet.
- Page uploads (logos, CSV files) are limited to 6 MB; logos to 300 KB. Runbooks up to 40 MB (sent straight to S3).
- Runbook rendering: kept are cell colours, bold/italic/colour, alignment, merged cells, numbered and bulleted
  lists, code blocks with a Copy button, command strips, coloured bands, labels and PNG/JPEG/GIF images.
  Not reproduced: EMF/WMF drawings (shown as a small "[image]" note), text boxes and shapes, page headers and
  footers, footnotes, exact fonts and page breaks. The publish message lists anything left out.
- Runbook images (`/media/...`) are served by CloudFront without sign-in, at long random addresses, so they
  load fast on a projector. Do not put anything secret in a runbook image.
- Live data limits (from ROBOMY DSE): 30 requests per minute per student key; pages of up to 5,000 rows.
