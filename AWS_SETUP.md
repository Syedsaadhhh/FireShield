# FireShield AWS Integration Setup, Least-Privilege IAM, & Budget Guardrails (`AWS_SETUP.md`)

> **Current Status:** `PACKAGE_PRESENT_CONFIG_AND_DEPLOYMENT_PENDING` (`boto3 1.43.103` installed locally; zero AWS resources created or billed in this run).

---

## 1. Credential Hygiene & Authentication Policy

1. **No Static Access Keys:** Never create long-lived IAM user access keys (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`), never store secrets in `.env`, and never paste credential values into chat or logs.
2. **Recommended Local Authentication:** Use **AWS IAM Identity Center (SSO)** via the AWS CLI:
   ```powershell
   aws configure sso --profile fireshield-dev
   aws sso login --profile fireshield-dev
   $env:AWS_PROFILE = "fireshield-dev"
   ```
3. **Recommended Compute Authentication:** Lambda, Step Functions, AWS Batch workers, and MediaConvert jobs must authenticate exclusively via scoped **IAM Execution Roles** with temporary STS credentials.

---

## 2. Non-Secret Configuration Handoff Checklist

Before transitioning from `dry_run=True` to live AWS deployment, the account owner must populate `.env` (copied from [`.env.example`](file:///C:/Users/ATEC/Desktop/FireShield/.env.example)) with the following 12 non-secret values:

| Environment Variable | Required Resource / Format | Purpose |
| :--- | :--- | :--- |
| `FIRESHIELD_AWS_REGION` | e.g., `us-east-1` | Target region for all media and workflow services. |
| `FIRESHIELD_S3_MEDIA_BUCKET` | S3 bucket name | Stores authorized mezzanine inputs and packaged HLS outputs + `.fireshield.json` sidecars. |
| `FIRESHIELD_S3_INPUT_PREFIX` | `ingest/mezzanine/` | S3 prefix scoped for incoming source video uploads. |
| `FIRESHIELD_S3_OUTPUT_PREFIX` | `published/renditions/` | S3 prefix scoped for MediaConvert HLS playlists and signed v2 sidecars. |
| `FIRESHIELD_STATE_MACHINE_ARN` | `arn:aws:states:...` | Step Functions state machine deployed from [`verification_pipeline.json`](file:///C:/Users/ATEC/Desktop/FireShield/verification_pipeline.json). |
| `FIRESHIELD_BATCH_JOB_QUEUE_ARN` | `arn:aws:batch:...` | AWS Batch Fargate/EC2 job queue for container-based frame screening. |
| `FIRESHIELD_BATCH_JOB_DEFINITION_ARN` | `arn:aws:batch:...` | Container job definition running `screening_analyzer.py`. |
| `FIRESHIELD_DYNAMODB_CONTRACTS_TABLE` | `FireShield-AssetContracts` | DynamoDB table (`PK: pk`, `SK: sk`) storing idempotency locks and `approved`/`rejected` states. |
| `FIRESHIELD_MEDIACONVERT_ROLE_ARN` | `arn:aws:iam::...:role/...` | IAM service role assumed by AWS Elemental MediaConvert. |
| `FIRESHIELD_MEDIACONVERT_ENDPOINT_URL` | `https://...mediaconvert...` | Account-specific MediaConvert regional endpoint. |
| `FIRESHIELD_SIGNING_KEY_ID` | e.g., `pub-01-ed25519-2026-q3` | Key identifier bound inside `schema_version: 2` sidecars and trusted by the Fire OS client. |
| `FIRESHIELD_CLOUDFRONT_BASE_URL` | `https://<id>.cloudfront.net` | CloudFront OAC distribution serving HLS playlists and sidecars to the Fire OS app. |

---

## 3. Least-Privilege IAM Role Boundaries

Each service role must be restricted to exact resource ARNs and prefixes:

1. **`FireShield-StepFunctionsOrchestratorRole`**:
   - `lambda:InvokeFunction` scoped to `FireShield-IngestIdempotency`, `FireShield-EditorialQueue`, `FireShield-TimelineMapper`, `FireShield-SidecarPublisher`, and `FireShield-QuarantineHandler`.
   - `batch:SubmitJob`, `batch:DescribeJobs`, `batch:TerminateJob` scoped to `FIRESHIELD_BATCH_JOB_QUEUE_ARN` and `FIRESHIELD_BATCH_JOB_DEFINITION_ARN`, plus `events:PutTargets`/`events:PutRule` for `.sync` job completion.
   - `mediaconvert:CreateJob`, `mediaconvert:GetJob` and `iam:PassRole` scoped strictly to `FIRESHIELD_MEDIACONVERT_ROLE_ARN`.
   - `dynamodb:PutItem` scoped to `FIRESHIELD_DYNAMODB_CONTRACTS_TABLE` for terminal rejection records.

2. **`FireShield-BatchScreeningWorkerRole`**:
   - `s3:GetObject` scoped strictly to `arn:aws:s3:::${FIRESHIELD_S3_MEDIA_BUCKET}/${FIRESHIELD_S3_INPUT_PREFIX}*`.
   - `dynamodb:UpdateItem` scoped to `FIRESHIELD_DYNAMODB_CONTRACTS_TABLE` to write candidate findings (`review_state = "pending_review"`).
   - **No** write access to `published/renditions/` and **no** access to signing keys (the screening worker can never publish or sign a sidecar).

3. **`FireShield-MediaConvertExecutionRole`**:
   - `s3:GetObject` on `${FIRESHIELD_S3_INPUT_PREFIX}*`.
   - `s3:PutObject` on `${FIRESHIELD_S3_OUTPUT_PREFIX}*`.

4. **`FireShield-SidecarPublisherRole`**:
   - `dynamodb:GetItem` and `dynamodb:PutItem` on `FIRESHIELD_DYNAMODB_CONTRACTS_TABLE` (verifies `review_state == "approved"` before signing).
   - `kms:Sign` (or Secrets Manager read for Ed25519 private seed) scoped strictly to `FIRESHIELD_KMS_KEY_ARN`.
   - `s3:PutObject` scoped strictly to `arn:aws:s3:::${FIRESHIELD_S3_MEDIA_BUCKET}/${FIRESHIELD_S3_OUTPUT_PREFIX}*.fireshield.json`.

---

## 4. Budget & Cost Guardrails Before Live Execution

To prevent accidental runaway spend during hackathon testing:
1. **AWS Budgets Alert:** Configure an AWS Budget threshold at **\$10.00 USD** (80% actual / 100% forecasted) before running any AWS Batch or MediaConvert job.
2. ** AWS Batch Compute Limits:** Use **Fargate Spot** (or a single `c6i.large` max-vCPU=4 ceiling on the Batch Compute Environment) and enforce `"TimeoutSeconds": 3600` in Step Functions.
3. **Short Mezzanine Test Clips Only:** Restrict cloud pipeline tests to rights-cleared reference clips under **30 seconds (`<= 1080p`, `30 fps`)** so MediaConvert billing remains fractions of a cent per test run.
4. **S3 Lifecycle Expiry:** Attach an S3 Lifecycle rule expiring objects under `ingest/mezzanine/` and `published/renditions/test-*` after **7 days**.
5. **DynamoDB On-Demand (`PAY_PER_REQUEST`):** Avoid provisioned RCU/WCU hourly charges on `FireShield-AssetContracts`.
