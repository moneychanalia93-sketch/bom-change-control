# PLM, BOMs, change requests and release control, in plain English

This page explains the terms used in this repository. It is simplified: real companies differ in the details. Nothing here describes any one company's process.

## The problem

A company that builds hardware (a server, a cooling unit) needs everyone to agree on exactly what goes into the product: which parts, how many, which version of each. Purchasing buys from that list, factories build from it, and technicians repair using it. If the list is wrong, the wrong parts get bought or built. If the list changes without anyone noticing, two teams build different products and call both "version 1".

## PLM

**Product Lifecycle Management (PLM)** is the system of record for that list and for the rules around changing it. Engineers and configuration analysts create parts, document them, and release them for use. The system remembers who changed what, when, and why. "Lifecycle" means a part has stages, from idea to retired.

This repository is a small teaching model of that idea. It is not a PLM product.

## Part and part number

A **part** is any item the company tracks, from a screw to a whole server. Each has a unique **part number** (here `CFG-30001`). The number is how every other system refers to it, which is why a duplicate number is a serious data error: two different things share one name.

## Assembly and BOM

An **assembly** is a part made of other parts. A **bill of materials (BOM)** is the recipe: for each assembly, the list of parts it contains and how many of each.

```
Fan tray assembly CFG-13000
  2 x Fan, 80 mm              CFG-13100
  1 x Fan tray frame          CFG-13200
  6 x Screw M3x6              CFG-30001
```

BOMs nest. The server chassis contains three fan trays; each fan tray contains two fans. To find out how many fans one chassis needs, you multiply down the levels (3 x 2 = 6). That is **explosion** (also called flattening): turning the nested recipe into one shopping list of bottom-level parts. A BOM that contains itself, even through several levels, can never be flattened. That is a **circular structure**.

## Revision

When a part or assembly changes, it gets a new **revision** (A, then B, and so on). Revision A and revision B are different versions of the same part number. Revisions let a factory know which version a given unit was built to.

## Lifecycle status

Each part has a status:

* **Draft**: still being worked on. Not approved for anyone to build or buy.
* **Released**: approved for use.
* **Obsolete**: retired. Existing products may still contain it; new designs should not.

## Release and release control

To **release** a part is to formally approve it for use. **Release control** is the set of rules that keep releases safe. The most basic rule: **a released assembly must not depend on something that is not released**. If a Released assembly contains a Draft part, then something approved for production relies on something nobody has approved. The data-quality check `released_with_draft_child` finds exactly that.

## Engineering change request (ECR)

Once something is released, nobody edits it directly. Changes go through an **engineering change request (ECR)**: a written proposal such as "remove part X from assembly Y" or "change the quantity of Z from 4 to 6", with a reason. (Some companies split this into a request and a later "change order"; this repository uses one record, called an ECR.)

An ECR moves through states:

1. **Draft**: being written by the **requester**.
2. **Submitted**: sent for review to a list of **approvers** (for example the owners of affected parts).
3. **Approved**: every approver has said yes.
4. **Released**: the change is applied to the BOM and the assembly's revision goes up.
5. **Rejected**: an approver said no. The requester can reopen it as a Draft, rework it, and submit it again.

Two rules matter most. First, **the requester cannot approve their own change**: approval only means something if a second person checked the work. Second, the history is **append-only**: entries are added, never edited or deleted, so there is always a trustworthy record of who did what and when. That record is the **audit trail**.

## Impact analysis (where-used)

Before approving a change, reviewers ask: what else does this touch? **Where-used** answers by walking up the BOM. If a screw changes, every assembly that contains it is affected, and so is every assembly that contains those, all the way to the finished product. Seeing every affected level is **impact analysis**.

## Data quality

BOM data is entered by people and imported from other systems, so it contains mistakes: a reference to a part that was never created, a quantity of zero, a unit entered as meters where the part is counted in each. **Data quality checks** scan for these. This repository reports the **error rate per 1,000 parts**, so a small BOM and a large one can be compared, and tracks it by week to show whether quality is improving.

## Cycle time and recurring issues

**Cycle time** is how long a change takes from submission to release, here measured in days. A rising cycle time suggests approvals are stuck. **Recurring issues** are problems that keep coming back (the same check failing run after run, or the same kind of change being rejected). Counting them by category and by owner shows where to fix the process rather than the individual record.

## How this repository maps to the terms

| Term | Where it lives |
|---|---|
| Parts, BOM, revision, status | `bomcc/bom.py`, `data/sample_bom.csv` |
| Explosion, where-used | `Bom.explode`, `Bom.where_used` in `bomcc/bom.py` |
| Data quality checks | `bomcc/validate.py` |
| ECR, approvers, states, audit trail, release | `bomcc/change.py` |
| Cycle time, recurring issues, error rate | `bomcc/report.py` |
