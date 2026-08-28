# Splunk SPL detection

Create `query.spl` and `submission.json`. Search `index=auth` over the previous 15 minutes for failed events, exclude `src=10.10.10.5`, count by `src` and `user`, require at least 8 failures, and retain the latest event time.
