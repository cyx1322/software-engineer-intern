# AI interaction record

Record interactions that influenced architecture, implementation decisions, or generated code. Include prompt excerpts, conversation links, or exported records. Routine lookups do not require entries.

Complete one entry per relevant interaction using the template below. Replace bracketed prompts and add entries as needed. If AI did not materially influence the code, state that and remove the blank entry.

## Interaction 1: Module 1 core API

| Field | Description |
|---|---|
| Tool or model | Claude Code (Claude Opus 5.5) |
| Prompt or record | I asked Claude to read the exercise instructions and implement Module 1. I set the key decisions: MongoDB as the database, a FastAPI multipart upload for the CSV, and a clearly documented MongoDB schema. I also chose the test coverage: invalid CSV rows with counts and rejection reasons, the successful case lifecycle, and four invalid transitions. These were a stage 2 review before stage 1 finishes, a review before OCR finishes, a repeated decision, and a stage 2 review after a stage 1 rejection. |
| Affected files | `q1_api/app/*.py`, `q1_api/tests/*.py`, `q1_api/README.md`, `q1_api/pytest.ini`, `requirements.txt`, `.gitignore`, `REFLECTION.md` |

### Adopted output

Claude generated the Module 1 implementation: CSV validation (`validation.py`), the transition table (`state_machine.py`), MongoDB access with an atomic status update (`db.py`), the batch and review routers, response models, the pytest suite with a mongomock fixture, and the schema documentation in `q1_api/README.md`. It also added `pythonpath = .` to `pytest.ini`, because the scaffold test could not import `app` when run from the repository root.

### Changes and rejected output

I reviewed the generated code and found two problems. Claude fixed both at my request.

- **Row numbers drifted after multi-line values.** `validate_csv()` took each row number from `reader.line_num`, which counts physical lines in the file. A quoted value with an embedded line break spans two lines but fills only one spreadsheet row. So every row below it was numbered one higher than the row staff would see. The docstring and the README both describe the number as the spreadsheet row, so the code disagreed with its own documentation. Claude changed the validator to count CSV records, starting from the header as row 1 and counting blank lines. It also added `test_row_numbers_are_spreadsheet_rows_not_file_lines`. Parse errors for malformed CSV still give the file line, since that is what an editor displays.
- **One of the exercise's 409 examples was untested.** Reviewing an `ocr_failed` case already returned 409. The exercise names this case as an example, but the suite had no test for it. Claude added `test_review_of_ocr_failed_case_is_409`, which checks the reason and that the status does not change.
