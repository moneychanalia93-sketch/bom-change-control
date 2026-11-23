# Sample data

`sample_bom.csv` is fictional. It holds 60 parts and 69 BOM lines for a rack-mount server chassis (`CFG-10001`) and a cooling loop controller (`CFG-20001`), each in three levels (product, assembly, component). It also has a Draft cable-arm assembly (`CFG-17000`) and an Obsolete legacy fan tray (`CFG-10900`).

## The two planted defects

Everything else in the file passes every check. The two defects are deliberate:

1. **Missing child.** Fan tray assembly `CFG-13000` has a BOM line for `CFG-13500`, a part that is not defined anywhere. Caught by `missing_children`.
2. **Release-control error.** Drive cage assembly `CFG-14000` is Released but contains `CFG-14300` (Drive tray latch, redesign), which is still Draft. Caught by `released_with_draft_child`.

`python -m bomcc validate` after loading this file reports exactly these two findings.

## Columns

`record,part_number,description,revision,status,uom,owner,child_part,quantity,unit,child_rev`

* `PART` rows use `part_number` through `owner`.
* `BOM` rows use `part_number` (the parent), `child_part`, `quantity`, `unit` and `child_rev`.
