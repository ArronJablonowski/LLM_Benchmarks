# Elastic ES|QL detection

Create `query.esql` and `submission.json`. Query `logs-endpoint.events.*` for process starts whose command line contains `-encodedcommand`, aggregate by host and user, and keep groups with at least 3 events.
