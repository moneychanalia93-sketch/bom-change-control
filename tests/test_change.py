import sqlite3
import unittest

from bomcc import change
from bomcc.change import ChangeError, InvalidTransition
from bomcc.db import load_bom
from tests.helpers import L, P, T, small_parts, small_lines, small_store, store


def new_ecr(conn: sqlite3.Connection, **over) -> change.Ecr:
    """A valid change by default: set SCREW in SUB1 to 5."""
    args = dict(title="More screws", category="Design change", assembly="SUB1", action="change_qty",
                child="SCREW", new_qty=5, requester="req", approvers=["ap1", "ap2"], now=T(2))
    args.update(over)
    return change.create_ecr(conn, **args)


def approved(conn: sqlite3.Connection, **over) -> change.Ecr:
    ecr = new_ecr(conn, **over)
    change.submit(conn, ecr.ecr_id, "req", T(3))
    change.approve(conn, ecr.ecr_id, "ap1", T(4))
    return change.approve(conn, ecr.ecr_id, "ap2", T(5))


class CreateTests(unittest.TestCase):
    def test_new_change_is_draft_with_an_id_and_a_history_entry(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        self.assertEqual((ecr.ecr_id, ecr.state), ("ECR-0001", "Draft"))
        self.assertEqual([e.action for e in change.history(conn, ecr.ecr_id)], ["create"])
        self.assertEqual(new_ecr(conn).ecr_id, "ECR-0002")

    def test_requester_cannot_be_an_approver(self) -> None:
        with self.assertRaisesRegex(ChangeError, "requester cannot be on the approver list"):
            new_ecr(small_store(), approvers=["req", "ap1"])

    def test_needs_at_least_one_approver_and_a_known_action(self) -> None:
        conn = small_store()
        with self.assertRaisesRegex(ChangeError, "approver"):
            new_ecr(conn, approvers=[])
        with self.assertRaisesRegex(ChangeError, "action"):
            new_ecr(conn, action="rename")

    def test_unknown_assembly_is_refused(self) -> None:
        with self.assertRaisesRegex(ChangeError, "not found"):
            new_ecr(small_store(), assembly="NOPE")

    def test_cannot_remove_or_change_a_child_the_assembly_lacks(self) -> None:
        conn = small_store()
        with self.assertRaisesRegex(ChangeError, "does not contain"):
            new_ecr(conn, action="remove", child="PLATE", assembly="SUB2")
        with self.assertRaisesRegex(ChangeError, "does not contain"):
            new_ecr(conn, assembly="SUB2", child="PLATE")

    def test_cannot_add_a_child_that_is_already_there(self) -> None:
        with self.assertRaisesRegex(ChangeError, "already contains"):
            new_ecr(small_store(), action="add", child="SCREW")

    def test_change_qty_needs_a_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ChangeError, "greater than zero"):
            new_ecr(small_store(), new_qty=0)

    def test_release_control_blocks_adding_a_draft_part_to_a_released_assembly(self) -> None:
        conn = store(small_parts() + [P("NEWPART", status="Draft")], small_lines())
        with self.assertRaisesRegex(ChangeError, "release control"):
            new_ecr(conn, action="add", assembly="SUB2", child="NEWPART", new_qty=1)

    def test_adding_a_part_that_would_loop_is_refused(self) -> None:
        with self.assertRaisesRegex(ChangeError, "circular"):
            new_ecr(small_store(), action="add", assembly="SUB1", child="TOP", new_qty=1)


class StateMachineTests(unittest.TestCase):
    def test_full_path_draft_to_released(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        states = [ecr.state]
        states.append(change.submit(conn, ecr.ecr_id, "req", T(3)).state)
        states.append(change.approve(conn, ecr.ecr_id, "ap1", T(4)).state)
        states.append(change.approve(conn, ecr.ecr_id, "ap2", T(5)).state)
        states.append(change.release(conn, ecr.ecr_id, "ap1", T(6)).state)
        self.assertEqual(states, ["Draft", "Submitted", "Submitted", "Approved", "Released"])

    def test_invalid_transitions_are_refused_and_change_nothing(self) -> None:
        conn = small_store()
        draft = new_ecr(conn)
        cases = {
            "approve a Draft": lambda: change.approve(conn, draft.ecr_id, "ap1"),
            "release a Draft": lambda: change.release(conn, draft.ecr_id, "ap1"),
            "reject a Draft": lambda: change.reject(conn, draft.ecr_id, "ap1", "no"),
            "reopen a Draft": lambda: change.reopen(conn, draft.ecr_id, "req"),
        }
        for label, action in cases.items():
            with self.subTest(label), self.assertRaises(InvalidTransition):
                action()
        self.assertEqual(change.get_ecr(conn, draft.ecr_id).state, "Draft")

    def test_cannot_submit_twice_or_release_before_approval(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        with self.assertRaises(InvalidTransition):
            change.submit(conn, ecr.ecr_id, "req")
        with self.assertRaises(InvalidTransition):
            change.release(conn, ecr.ecr_id, "ap1")

    def test_released_is_final(self) -> None:
        conn = small_store()
        ecr = approved(conn)
        change.release(conn, ecr.ecr_id, "ap1")
        for action in (lambda: change.release(conn, ecr.ecr_id, "ap1"),
                       lambda: change.reject(conn, ecr.ecr_id, "ap1", "late"),
                       lambda: change.approve(conn, ecr.ecr_id, "ap1"),
                       lambda: change.reopen(conn, ecr.ecr_id, "req")):
            with self.assertRaises(InvalidTransition):
                action()

    def test_transition_table_matches_the_documented_machine(self) -> None:
        self.assertEqual(change.TRANSITIONS["Draft"], {"Submitted"})
        self.assertEqual(change.TRANSITIONS["Submitted"], {"Approved", "Rejected"})
        self.assertEqual(change.TRANSITIONS["Approved"], {"Released", "Rejected"})
        self.assertEqual(change.TRANSITIONS["Rejected"], {"Draft"})
        self.assertEqual(change.TRANSITIONS["Released"], set())

    def test_only_the_requester_can_submit_or_reopen(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        with self.assertRaisesRegex(ChangeError, "only the requester"):
            change.submit(conn, ecr.ecr_id, "ap1")
        change.submit(conn, ecr.ecr_id, "req")
        change.reject(conn, ecr.ecr_id, "ap1", "unclear")
        with self.assertRaisesRegex(ChangeError, "only the requester"):
            change.reopen(conn, ecr.ecr_id, "ap1")


class ApprovalTests(unittest.TestCase):
    def test_requester_cannot_approve_their_own_change(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        with self.assertRaisesRegex(ChangeError, "cannot approve their own change"):
            change.approve(conn, ecr.ecr_id, "req")
        self.assertEqual(change.get_ecr(conn, ecr.ecr_id).state, "Submitted")
        self.assertNotIn("approve", [e.action for e in change.history(conn, ecr.ecr_id)])

    def test_someone_off_the_approver_list_cannot_approve(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        with self.assertRaisesRegex(ChangeError, "not on the approver list"):
            change.approve(conn, ecr.ecr_id, "stranger")

    def test_every_listed_approver_must_approve_and_only_once(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        self.assertEqual(change.approve(conn, ecr.ecr_id, "ap1").state, "Submitted")
        with self.assertRaisesRegex(ChangeError, "already approved"):
            change.approve(conn, ecr.ecr_id, "ap1")
        self.assertEqual(change.approve(conn, ecr.ecr_id, "ap2").state, "Approved")

    def test_reject_needs_an_approver_and_a_reason(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        with self.assertRaisesRegex(ChangeError, "not on the approver list"):
            change.reject(conn, ecr.ecr_id, "req", "I changed my mind")
        with self.assertRaisesRegex(ChangeError, "reason"):
            change.reject(conn, ecr.ecr_id, "ap1", "  ")
        self.assertEqual(change.reject(conn, ecr.ecr_id, "ap1", "unclear").state, "Rejected")

    def test_approved_change_can_still_be_rejected(self) -> None:
        conn = small_store()
        ecr = approved(conn)
        self.assertEqual(change.reject(conn, ecr.ecr_id, "ap2", "found a conflict").state, "Rejected")

    def test_rework_starts_approvals_from_zero(self) -> None:
        conn = small_store()
        ecr = new_ecr(conn)
        change.submit(conn, ecr.ecr_id, "req")
        change.approve(conn, ecr.ecr_id, "ap1")
        change.reject(conn, ecr.ecr_id, "ap2", "needs rework")
        change.reopen(conn, ecr.ecr_id, "req")
        change.submit(conn, ecr.ecr_id, "req")
        self.assertEqual(change.approve(conn, ecr.ecr_id, "ap2").state, "Submitted")  # ap1's old approval is gone
        self.assertEqual(change.approve(conn, ecr.ecr_id, "ap1").state, "Approved")


class ReleaseTests(unittest.TestCase):
    def test_change_qty_updates_the_line_and_bumps_the_revision(self) -> None:
        conn = small_store()
        ecr = approved(conn)
        change.release(conn, ecr.ecr_id, "ap1", T(6))
        bom = load_bom(conn)
        self.assertEqual(bom.line("SUB1", "SCREW").quantity, 5)
        self.assertEqual(bom.part("SUB1").revision, "B")
        self.assertEqual(change.history(conn, ecr.ecr_id)[-1].note, "SUB1 revision A -> B")

    def test_only_the_edited_assembly_changes_revision(self) -> None:
        conn = small_store()
        change.release(conn, approved(conn).ecr_id, "ap1")
        self.assertEqual(load_bom(conn).part("TOP").revision, "A")

    def test_add_inserts_a_line_using_the_part_defaults(self) -> None:
        conn = small_store()
        ecr = approved(conn, action="add", assembly="SUB2", child="PLATE", new_qty=2)
        change.release(conn, ecr.ecr_id, "ap1")
        line = load_bom(conn).line("SUB2", "PLATE")
        self.assertEqual((line.quantity, line.unit, line.child_rev), (2, "EA", "A"))

    def test_remove_deletes_the_line(self) -> None:
        conn = small_store()
        ecr = approved(conn, action="remove", child="PLATE", new_qty=None)
        change.release(conn, ecr.ecr_id, "ap1")
        self.assertIsNone(load_bom(conn).line("SUB1", "PLATE"))

    def test_change_rev_pins_the_child_revision(self) -> None:
        conn = small_store()
        ecr = approved(conn, action="change_rev", child="PLATE", new_qty=None, new_rev="C")
        change.release(conn, ecr.ecr_id, "ap1")
        self.assertEqual(load_bom(conn).line("SUB1", "PLATE").child_rev, "C")

    def test_stale_change_is_refused_at_release_and_nothing_is_written(self) -> None:
        conn = small_store()
        ecr = approved(conn)
        with conn:  # someone edits the BOM directly after approval
            conn.execute("DELETE FROM bom_lines WHERE parent = 'SUB1' AND child = 'SCREW'")
        with self.assertRaisesRegex(ChangeError, "does not contain"):
            change.release(conn, ecr.ecr_id, "ap1")
        self.assertEqual(change.get_ecr(conn, ecr.ecr_id).state, "Approved")
        self.assertEqual(load_bom(conn).part("SUB1").revision, "A")

    def test_unknown_change_id(self) -> None:
        with self.assertRaisesRegex(ChangeError, "no such change"):
            change.get_ecr(small_store(), "ECR-9999")


class AuditAndImpactTests(unittest.TestCase):
    def test_history_cannot_be_edited_or_deleted(self) -> None:
        conn = small_store()
        new_ecr(conn)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            conn.execute("UPDATE change_history SET actor = 'someone else'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            conn.execute("DELETE FROM change_history")

    def test_history_records_who_did_what_in_order(self) -> None:
        conn = small_store()
        ecr = approved(conn)
        self.assertEqual([(e.actor, e.action) for e in change.history(conn, ecr.ecr_id)],
                         [("req", "create"), ("req", "submit"), ("ap1", "approve"), ("ap2", "approve")])

    def test_impact_lists_the_assembly_and_every_level_above_it(self) -> None:
        parts = [P(x) for x in ("TOP", "MID", "LOW", "LEAF", "OTHER")]
        lines = [L("TOP", "MID"), L("MID", "LOW"), L("LOW", "LEAF")]
        conn = store(parts, lines)
        ecr = new_ecr(conn, assembly="LOW", child="LEAF", new_qty=3)
        impact = change.impact_of_change(load_bom(conn), ecr)
        self.assertEqual([(a.part_number, a.level) for a in impact],
                         [("LOW", 0), ("MID", 1), ("TOP", 2)])


if __name__ == "__main__":
    unittest.main()
