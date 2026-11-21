# Tabletop exercise: malware found in an uploaded file (T78)

A tabletop is a talk-through, not a live test. Nothing is deployed. Time needed: about 30 minutes, 3 to 6 people.
Roles: **facilitator** reads the scenario and the "injects" one at a time; **responders** say what they would do and where they would look; **scribe** writes the answers and the gaps.

## Scenario

Monday, 10:12. A clinic user uploads a document through the portal. The scanner flags it. Minutes later an email arrives
from the alarm topic with the subject `[HIGH] securecare: securecare-scan-infected-file`.

## Injects (read one at a time, wait for answers)

1. **The alert.** The email says: "A scan found malware; the file went to quarantine." First step in the message: look in the quarantine bucket and find who uploaded it.
   - Who reads the alert, and how fast must they respond? Is anyone assigned out of hours?
2. **Scope.** The object key shows a patient folder. Was it only one file? Check the scan results: count of infected files in the last hour, any `scan-failed` or `scan-dead-letter` alarms.
   - Where would you look? (CloudWatch dashboard `securecare-overview`, metric `ScansInfected`, scanner logs.)
3. **Spread.** Did the file reach the clean bucket before the scan finished? (It should not: files land in `uploads`, and only a clean verdict copies them out.)
   - How would you prove it? (Object list of the clean bucket, scanner log line for that key.)
4. **The uploader.** Who uploaded it: a real clinic user, a compromised account, or a test? The audit table and the caller id in logs say which account.
   - Who may disable the account? Who must be told (clinic contact, compliance)?
5. **A second alarm.** Ten minutes later `securecare-scan-dead-letter` fires. A scan request failed three times.
   - Read the message in the DLQ before re-driving. Is it related? Re-drive only after the cause is fixed.
6. **Close-out.** What is recorded, who signs it off, when is the file deleted from quarantine (retention), and what changes after this?

## What the scribe records

| Question | Answer from the group | Gap or action | Owner |
| --- | --- | --- | --- |
| Who is told first, and how fast? | | | |
| Do we know who uploaded the file? | | | |
| Can we prove nothing reached the clean bucket? | | | |
| Who disables an account, who informs the clinic? | | | |
| What is the retention for the quarantine copy? | | | |
| Is the playbook text in the alert enough for someone who has never seen the system? | | | |

## After the session

- Put the filled table and the date in the project documents (this is the T78 evidence).
- Any gap that needs a code or setting change becomes a task with an owner. The playbook texts live in `modules/incident_automation/lambda/handler.py` (`PLAYBOOK`); change them there, not in the table.
- The live version of this scenario is the EICAR demo in `docs/demo-runbook.md`.
