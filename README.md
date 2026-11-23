# bom-change-control

A small command-line tool that **models** how a Product Lifecycle Management (PLM) team keeps a bill of materials (BOM) correct: it loads a BOM, runs data-quality checks, controls changes through an approval workflow, and reports on recurring issues and cycle times.

> **What this is not.** This project models PLM change control. It is **not** Oracle Agile PLM, it is not a clone of any PLM product, and it does **not** connect to any PLM system. It is a self-contained Python program with its own SQLite database, written to show the ideas (BOM structure, release control, change requests, audit trail, data-quality metrics) in code you can read in one sitting.
>
> **All data is fictional.** The products (a rack-mount server chassis and a cooling loop controller), part numbers (`CFG-10001`...), and people are invented.

Standard library only. No installs. Python 3.11 or newer.

## 60-second quickstart

Run from the repository root.

```powershell
# Windows PowerShell. Use the full path to your python.exe if `python` is not on your PATH:
$py = "C:\Program Files\Python313\python.exe"      # change to wherever Python is installed
& $py -m bomcc demo
& $py -m unittest discover
```

```bash
# macOS / Linux
python3 -m bomcc demo
python3 -m unittest discover
```

`demo` loads the sample BOM, validates it (finds 2 planted defects), raises a change, shows its impact, tries a self-approval (blocked), approves, releases, re-validates, and prints the report. It takes a fraction of a second. Its timestamps are simulated (21 days are compressed into seconds) so cycle times and the weekly trend have something to show; nothing else in it is simulated.

To drive it step by step, see [Commands](#commands).

## Commands

All commands accept `--db PATH` (default `var/bomcc.db`, or the `BOMCC_DB` environment variable). `var/` and `*.db` are git-ignored.

| Command | What it does |
|---|---|
| `python -m bomcc load data/sample_bom.csv` | Replace the stored BOM with the CSV contents. |
| `python -m bomcc validate` | Run all checks, print findings, record the run for the report. Exit code 1 if any finding is an error. `--no-record` skips recording. |
| `python -m bomcc explode CFG-10001` | Flatten an assembly to total quantity per leaf part. `--tree` also prints the indented hierarchy. |
| `python -m bomcc impact CFG-30001` | Where-used: every assembly above a part, at every level. |
| `python -m bomcc change new --title ... --category ... --assembly ... --action add\|remove\|change_qty\|change_rev --child ... --by NAME --approvers A,B` | Create a change request (state Draft). Prints its impact. Extra options: `--qty`, `--unit`, `--rev`, `--reason`. |
| `python -m bomcc change submit ECR-0001 --by NAME` | Draft to Submitted. Requester only. |
| `python -m bomcc change approve ECR-0001 --by NAME` | Record one approval. Every listed approver must approve. |
| `python -m bomcc change reject ECR-0001 --by NAME --reason "..."` | Submitted or Approved to Rejected. Approvers only. |
| `python -m bomcc change reopen ECR-0001 --by NAME` | Rejected to Draft, for rework. Requester only. |
| `python -m bomcc change release ECR-0001 --by NAME` | Approved to Released: applies the edit and bumps the revision. |
| `python -m bomcc change list` / `change show ECR-0001` | List changes / show one with its impact and full history. |
| `python -m bomcc report [--csv out.csv]` | Recurring issues and metrics as text, and optionally CSV. |
| `python -m bomcc demo` | The whole story on fictional data. |

## Input format

One CSV file holds both parts and BOM lines, told apart by the `record` column (see `data/sample_bom.csv`):

```
record,part_number,description,revision,status,uom,owner,child_part,quantity,unit,child_rev
PART,CFG-13100,"Fan, 80 mm",A,Released,EA,J. Okafor,,,,
BOM,CFG-13000,,,,,,CFG-13100,2,EA,A
```

* `PART` rows define a part: number, description, revision, status (`Draft`, `Released` or `Obsolete`), unit of measure, owner.
* `BOM` rows say "the part in `part_number` (the parent) contains `quantity` x `child_part`", with the unit and the child revision the assembly uses.
* Zero or negative quantities, unknown children and duplicate part numbers are loaded as they are on purpose. Catching them is the validator's job.

## What each validation check catches

| Check | Severity | Catches |
|---|---|---|
| `missing_children` | error | A BOM line points at a part number that does not exist (or belongs to an unknown parent). |
| `duplicate_part_numbers` | error | The same part number is defined more than once. |
| `bad_quantities` | error | A BOM line has a quantity of zero or less. |
| `released_with_draft_child` | error | A **Released** assembly contains a **Draft** part. This is the release-control error: something approved for use depends on something not yet approved. |
| `circular_structures` | error | An assembly contains itself, directly or through other assemblies. Reported once per loop with its path. |
| `inconsistent_units` | warning | A BOM line's unit differs from the child part's own unit of measure (for example a line in `M` for a part defined in `EA`). |
| `orphaned_parts` | info | A part is not used by any assembly and has no components of its own. |

Each check has a passing and a failing test in `tests/test_validate.py`.

## How a change flows

```
            submit              approve (all listed approvers)         release
  Draft ------------> Submitted -------------------------> Approved ------------> Released
    ^                     |                                    |
    |   reopen            | reject                             | reject
    +---- Rejected <------+------------------------------------+
```

* **Enforced transitions.** Only the arrows above exist. Anything else (for example releasing a Submitted change, or approving a Draft) raises `InvalidTransition` and writes nothing.
* **The requester cannot approve their own change.** They also cannot be on the approver list. Every listed approver must approve before the change becomes Approved.
* **Append-only history.** Every create, submit, approve, reject, reopen and release is written to `change_history` with a timestamp and the actor. SQLite triggers refuse any `UPDATE` or `DELETE` on that table.
* **Impact analysis.** Creating a change prints every assembly affected: the edited assembly and every assembly above it, at every level.
* **Release applies the change.** `add`, `remove`, `change_qty` or `change_rev` is applied to the BOM and the edited assembly's revision goes up (`A` to `B`). Release re-checks the change against the current BOM, so a change that went stale is refused rather than half-applied. Adding a Draft part to a Released assembly is refused (release control).

## Sample output

Real output from `python -m bomcc validate` on the sample data:

```
ERROR   missing_children           CFG-13000 lists child CFG-13500, which is not a known part  [owner: J. Okafor]
ERROR   released_with_draft_child  Released assembly CFG-14000 contains Draft part CFG-14300  [owner: M. Lindqvist]
2 finding(s), 2 error(s), across 60 parts.
```

From `python -m bomcc demo`, the change and its history:

```
ECR-0001  [Draft]  Remove dangling reference CFG-13500 from fan tray
  change   : remove CFG-13500 from CFG-13000
  category : Data correction   requester: j.okafor   approvers: m.lindqvist, s.tanaka
Impact (every assembly affected, at every level):
  level 0  CFG-13000    via CFG-13000
  level 1  CFG-10001    via CFG-10001 > CFG-13000

Blocked: the requester cannot approve their own change

  2026-11-06T09:00:00+00:00  j.okafor     create   Draft -> Draft  (Remove dangling reference CFG-13500 from fan tray)
  2026-11-06T09:00:00+00:00  j.okafor     submit   Draft -> Submitted
  2026-11-07T09:00:00+00:00  m.lindqvist  approve  Submitted -> Submitted  (1 of 2 approvals)
  2026-11-08T09:00:00+00:00  s.tanaka     approve  Submitted -> Approved  (2 of 2 approvals)
  2026-11-09T09:00:00+00:00  m.lindqvist  release  Approved -> Released  (CFG-13000 revision A -> B)
```

And the report (dates in the demo are relative to the day you run it):

```
Summary
  Parts checked (latest run)                  : 60
  Open changes (Draft, Submitted, Approved)   : 1
  Released changes                            : 2
  Changes rejected at least once              : 1
  Changes reworked (reopened)                 : 1
  Cycle time in days (Submitted to Released)  : mean 2.50, median 2.50, n=2
  Data-quality errors (latest run)            : 1
  Error rate per 1,000 parts (latest run)     : 16.7

Top recurring issues (validation findings across all runs; rejected/reworked changes)
  rank  kind        issue                      count
  ----  ----------  -------------------------  -----
  1     validation  released_with_draft_child  3
  2     change      Design change              1
  3     validation  missing_children           1
```

How the numbers are defined:

* **Cycle time** is the days from a change's last `submit` event to its `release` event, taken from the history timestamps. Mean and median are over released changes.
* **Error rate** is validation errors in the latest run divided by parts in that run, times 1,000. Warnings and info are not counted.
* **Recurring issues** count findings across every recorded validation run (a defect left unfixed across three runs counts three times, which is what makes it recurring) plus rejected or reworked changes grouped by category. Owners come from the part's `owner` column for findings and from the requester for changes.
* **Trend** is per ISO week: runs, errors and error rate from the last run that week, changes released, and their mean cycle time.

`--csv` writes the same numbers as rows of `section,key,detail,value`.

## Project layout

See [docs/PLM_PROCESS.md](docs/PLM_PROCESS.md) for a plain-English explanation of PLM, BOMs, change requests and release control.

## Sample data

`data/sample_bom.csv` has 60 parts and 69 BOM lines in three levels (product, assembly, component) for two fictional products, plus a Draft cable-arm assembly and an Obsolete legacy fan tray. It contains **exactly two planted defects**; every other check passes on it:

1. `CFG-13000` (Fan tray assembly) lists a child `CFG-13500` that is not defined anywhere. Caught by `missing_children`.
2. `CFG-14000` (Drive cage assembly, Released) contains `CFG-14300` (Drive tray latch, Draft). Caught by `released_with_draft_child`.

## Limitations

* **Not a PLM system.** No connection to Oracle Agile PLM or any other PLM, no import from one, no export to one. The CSV format is this project's own.
* **No real identity.** Actor names (`--by`) are typed text. Nothing authenticates them, so the "requester cannot approve" rule is enforced against the names given, not against verified users.
* **Single user, single file.** One local SQLite database, no concurrency control, no web UI.
* **Simplified change model.** One edit per change request; every listed approver must approve (no voting rules, delegation or deadlines). Release revises only the edited assembly; assemblies above it are listed as impacted but not revised automatically.
* **Revisions are letters or numbers** that count up (`A`, `B`, ..., `Z`, `AA`). There are no effectivity dates, no documents or drawings, no part-number generation, and no cost or supplier data.
* **Checks cover what is listed above.** For example it does not flag a Released assembly that contains an Obsolete part, or a BOM line pinned to an older child revision than the part's current one.
* **Explosion is not memoised.** Fine for BOMs of this size; a very large, heavily shared structure would want a cache.
* **Demo timestamps are simulated**, as described above. The CI workflow targets Python 3.11, 3.12 and 3.13.

## License

MIT. See [LICENSE](LICENSE).
