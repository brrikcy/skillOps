# Note for the AWS administrator: let SkillOps deploy from GitHub

**What this is.** SkillOps is a small serverless web app (Lambda, API Gateway, DynamoDB, S3, CloudFront, Cognito) for
Robomy's trainers. Its code is in the GitHub repository `brrikcy/skillOps`. The person who owns the app does not have AWS
access, so we ask you to do **one thing, once** (about 10 minutes): create a role that only that repository can use.
After that you are not needed for deploys or updates, and **no AWS access key is created or shared**.

## What you create

One CloudFormation stack from `deploy/github-oidc-role.yaml` in this repository. It makes:

1. **GitHub as a trusted sign-in provider** (`token.actions.githubusercontent.com`) for the account. Skip this part if
   the account already has it (see "If GitHub is already a provider" below).
2. **A role named `SkillOpsGitHubDeploy`.** It can be assumed only by a GitHub Actions job that
   runs in repository `brrikcy/skillOps` **and** in its GitHub environment `production`. Any other repository, branch or
   person is refused by AWS. Sessions last one hour.

By default the role has `AdministratorAccess`, because the first deploy creates IAM roles, CloudFront, Cognito, DynamoDB,
Lambda and more. If you prefer a narrower policy, give its ARN in the `PolicyArn` parameter. The stack's own list of
resources is in `template.yaml` (section 1 of `docs/AWS_SETUP.md` explains each one).

## Steps

Use the region `ap-south-1` (Mumbai). Either way works:

**A. In the console.** CloudFormation → Create stack → With new resources → Upload a template file →
`deploy/github-oidc-role.yaml`. Stack name `skillops-github-deploy-role`. Leave the parameters as they are (change them
only as described below). Tick the box that acknowledges IAM resources with custom names, then create the stack. When it
shows `CREATE_COMPLETE`, open **Outputs** and copy `RoleArn`.

**B. With the CLI.**

```bash
aws cloudformation deploy --region ap-south-1 --stack-name skillops-github-deploy-role \
  --template-file deploy/github-oidc-role.yaml --capabilities CAPABILITY_NAMED_IAM
aws cloudformation describe-stacks --region ap-south-1 --stack-name skillops-github-deploy-role \
  --query "Stacks[0].Outputs" --output table
```

## What to send back

The `RoleArn` (it looks like `arn:aws:iam::<account id>:role/SkillOpsGitHubDeploy`). It is not a secret.

## Parameters, if you need to change them

| Parameter | Default | Change it when |
|---|---|---|
| `GitHubRepository` | `brrikcy/skillOps` | the repository has moved. Write it exactly as GitHub shows it: **capital letters count**. |
| `GitHubEnvironment` | `production` | you want another environment name (it must match the one set in GitHub). |
| `CreateOidcProvider` | `true` | the account already has the GitHub provider: use `false`. |
| `PolicyArn` | `AdministratorAccess` | you want a narrower policy. |

### If GitHub is already a provider

IAM → Identity providers lists `token.actions.githubusercontent.com` when it exists. Run the stack with
`CreateOidcProvider=false` (CLI: add `--parameter-overrides CreateOidcProvider=false`). The role is then added next to
the existing provider.

## What the deploy then does

When the app's owner presses the deploy button in GitHub, the job signs in as this role, builds the functions and runs
`sam deploy` for one stack named `skillops` in `ap-south-1`. It creates the app's tables, buckets, functions, user pool,
CloudFront distribution, alarms and a monthly budget alert. Running cost is about US$2–4 a month in use and about $1 idle
(section 11 of `docs/AWS_SETUP.md`). The owner receives the first Supervisor sign-in by email (Cognito) and the alarm
emails; you do not need to be on any of them.

## Things that can come up

- **First CloudFront distribution in a new account.** AWS sometimes asks new accounts to verify themselves before
  CloudFront can be used. If the stack fails on the CloudFront resource with a message about account verification,
  contact AWS Support; nothing in the app needs to change.
- **Removing access later.** Delete the `skillops-github-deploy-role` stack, or just the role. The deployed app keeps
  running; only the deploy button stops working.
- **Removing the app.** Deleting the `skillops` stack removes every table, function, bucket and user account, and all
  data in them. Ask the owner to export first (Export page in the app).
