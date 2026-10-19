# Implementation reflection

Complete each section before submission. Replace bracketed prompts with decisions, measurements, or evidence. Use `Not completed` or `Not measured` where applicable, and explain the gap. Reviewers assess this record alongside the code.

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | MongoDB 8 through `pymongo`, as the exercise recommends. A case is one document that gains OCR fields in Module 2, so a schemaless document store fits without migrations. `find_one_and_update` gives an atomic compare-and-set on `status`, which is all the review workflow needs, so a standalone server works without multi-document transactions. Tests use `mongomock` so they pass without a server, and the same suite runs against real MongoDB with `TEST_MONGO_URL`. The schema is in [q1_api/README.md](q1_api/README.md#mongodb-schema). |
| Background OCR processing | [Describe how work is scheduled and how interrupted cases are processed again. Explain the approach.] |
| Review state machine | `q1_api/app/state_machine.py` owns the rules. `TRANSITIONS` maps `(current status, event)` to the next status, and `next_status()` is the only lookup. A status with no outgoing entry is terminal, so rejected cases cannot reach stage 2 or export. `db.transition_case()` applies a transition with an update filtered on the status it checked, so a concurrent second decision gets `409` instead of overwriting the first. Adding a workflow step, such as returning a case to stage 1, means a new table entry and an endpoint that sends the new event. |
| Delivery idempotency key | [Identify the source of the key. Explain why retries reuse it and different exports receive different keys.] |

## 2. Mock observations and retry configuration

| Item | Measurement or decision |
|---|---|
| Observed OCR failure rates | [Record attempt counts and outcome rates. Describe the measurement method and test configuration.] |
| OCR timeout | [Record the timeout in seconds and explain the value using observations.] |
| Retry limit | [Record the attempt limit and backoff behavior.] |
| Exhausted retries | [Describe the stored status and reason, how staff find failed cases, and the next operational action.] |

## 3. Verification evidence

Provide command output, excerpts, or links to artifacts for each check.

### OCR recovery

[Include `/debug/requests` records and a final-state comparison between a clean run and a run interrupted by a worker termination, then processed again. State the result.]

### Account-opening submission

[Include `/debug/deliveries` records showing exactly one ingestion per export, including delivery retries after a lost response.]

### Backup retention

[Include before-and-after file listings showing that only the seven most recent backups remain.]

## 4. AI-generated code requiring further review

[Identify AI-generated code you do not fully understand. Include file paths, the behavior in question, and the review still needed. Enter `None` if applicable.]

The assessment values accurate disclosure. The requirement to explain submitted code during the interview remains in effect.

## 5. Incomplete work and next steps

[List unfinished requirements, known failures, and their effect on the workflow. Describe what you would address with one additional day.]
