# Deploy SkillOps from GitHub (no AWS sign-in on your laptop)

For when you can push to GitHub but cannot sign in to AWS. An AWS administrator does one set-up step; after that you
deploy and update by pressing a button. The workflow is `.github/workflows/deploy.yml`. It never runs by itself.

```
 you press "Run workflow"  ->  GitHub runs the tests  ->  GitHub signs in to AWS as a role (no keys)  ->  builds  ->  deploys
```

## 1. Ask your AWS administrator (once)

Send them [AWS_ADMIN_NOTE.md](AWS_ADMIN_NOTE.md). They create one role from `deploy/github-oidc-role.yaml` and send you its
**RoleArn**. Nothing secret is shared.

## 2. Set up GitHub (once, in the repository's Settings)

1. **Environments → New environment →** name it exactly `production`.
   - *Deployment branches and tags:* choose "Selected branches" and add `main`.
   - *Required reviewers (optional):* add yourself or a colleague, so every deploy needs a second click.
2. **Secrets and variables → Actions → Variables → New repository variable.** None of these is secret:

| Variable | Value | Needed |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | the RoleArn from the administrator | yes |
| `SUPERADMIN_EMAIL` | your email: AWS sends the first Supervisor password here | yes |
| `SUPERADMIN_NAME` | your name as shown in the app | no (default "Supervisor") |
| `ALARM_EMAIL` | where alarm and budget emails go | no (default: your email) |
| `SUPPORT_EMAIL` | where people write for help; shows a "Send feedback" link in the app | no |
| `MONTHLY_BUDGET_USD` | cost warning level, for example `10` | no (default 10) |
| `WEB_DOMAIN_NAME`, `WEB_CERTIFICATE_ARN` | your own address (section 5) | no |

## 3. Deploy

**Actions → Deploy to AWS → Run workflow**, type `deploy`, press the green button (approve it if you added reviewers).

- *Run the tests* takes about 10 minutes.
- *Build and deploy* takes 10–20 minutes the first time (CloudFront is the slow part).
- At the end, the run's summary page lists **WebUrl**. That is the address of SkillOps.

## 4. After the first deploy

1. Confirm the email "AWS Notification - Subscription Confirmation", or alarm emails will not arrive.
2. Open **WebUrl**. Sign in with your email and the temporary password from the email "Your SkillOps account", then
   choose your own password (at least 10 characters, with a capital letter, a small letter and a number).
3. Open **Health** in the menu. Every table, both buckets and Cognito should show as working.
4. Set up your data in this order: **Courses** with projects, then **Institutions** with programmes and batches (or
   **Import from CSV**), then **Trainers**. Then **Runbooks → Add a runbook**: upload the Word file, **Map**, **Preview**,
   **Publish**, and attach the PDF. Choose the runbook for each project under **Courses**.

## 5. Updating, a custom address, going back

- **Update:** commit, push to `main`, then run the workflow again. Nothing is lost; only changed parts are updated.
- **Custom address** (for example `skillops.robomy.in`): the administrator requests a certificate in **US East
  (N. Virginia)** (a CloudFront rule) and sends you its ARN. Set `WEB_DOMAIN_NAME` and `WEB_CERTIFICATE_ARN`, run the
  workflow, then add a CNAME at your DNS provider pointing to the `CloudFrontDomain` value in the summary.
- **Go back to an earlier version:** revert the commit on `main` and run the workflow again. Data is kept; the app's
  data and its code are deployed separately.

## 6. If a run fails

| What you see | Usually means |
|---|---|
| "Not authorized to perform sts:AssumeRoleWithWebIdentity" | The role's trust does not match: check the repository name (**capital letters count**: `brrikcy/skillOps`), that the environment is exactly `production`, and that `AWS_DEPLOY_ROLE_ARN` is right. |
| "Set the variable ..." | A required variable from section 2 is missing. |
| The tests fail | Nothing is deployed. Open the failed step and send me the log. The tests were written on Windows, so a small difference on Linux is possible the first time. |
| "exec format error" while building | The arm64 emulation step did not run: re-run; if it repeats, send me the log. |
| The stack ends in `ROLLBACK` | CloudFormation undid the change. Open the stack's Events in AWS (ask the administrator) or send me the log's last lines. |
| CloudFront account verification message | A new AWS account must be verified; the administrator contacts AWS Support. |

## What this does not do

- It does not create the AWS account, the budget limit you want, or any custom domain; those stay with the administrator.
- Deleting the app (and all its data) is done by the administrator, on purpose: deleting the `skillops` stack removes every
  table. Export first from the app's Export page.
