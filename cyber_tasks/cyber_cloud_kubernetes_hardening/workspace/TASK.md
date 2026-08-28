# Cloud IAM and Kubernetes hardening

Create `hardened_iam.json` granting only `s3:GetObject` on `arn:aws:s3:::reports-prod/public/*`. Create `hardened_deployment.yaml` that preserves image `registry.example/report-api:1.4`, port 8080, and replicas 2 while enforcing non-root UID 10001, no privilege escalation, read-only root filesystem, dropped ALL capabilities, and seccomp RuntimeDefault. Write `submission.json` with `risks_fixed`.
