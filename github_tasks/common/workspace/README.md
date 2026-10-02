# Offline GitHub CLI benchmark lab

This task uses a fictional GitHub fixture. `github_lab.py` never executes `gh`,
accesses an account, or makes a network request. Use `python3 github_lab.py help`
and submit simulated commands with `python3 github_lab.py run '<command>'`.

Write `answer.json` with `commands` (in order), `findings` derived from the lab
output, and `actions` explaining the safe operation or next step. The hidden
grader checks both the answer and the lab transcript. Never run a real `gh`
command, use a real token, or contact GitHub for this benchmark.
