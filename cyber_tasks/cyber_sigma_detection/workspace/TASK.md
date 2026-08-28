# Sigma rule

Create `detection.yml` using Sigma rule structure and `submission.json` containing `technique_ids`, `logsource`, and `false_positive_notes`. Detect encoded PowerShell process creation, but exclude commands signed by the approved deployment path `C:\\Program Files\\Ops\\Deploy\\`.
