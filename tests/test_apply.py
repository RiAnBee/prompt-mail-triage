"""Directory application: message identity, dry-run and observable mailbox state."""

import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from email_triage.apply import Action, ApplyError, apply_plan, export_plan
from email_triage.imap_mailbox import encode_mailbox, list_mailbox_name, quote_mailbox
from email_triage.models import EmailRecord
from email_triage.redaction import message_ref


def record(uid, subject, body="正文"):
    return EmailRecord(source_id="imap:42:%s" % uid, folder="INBOX", subject=subject,
                       sender="someone@example.com", received_at="2026-01-01", body=body)


def actions():
    return [Action("118", "imap:42:118", "other", "Triage-other", "邀请"),
            Action("119", "imap:42:119", "account-access", "Triage-account", "账号")]


class MailboxState:
    """Small mailbox-state model, including overlapping UIDs in distinct folders."""
    def __init__(self, validity=b"42", atom_list=False):
        self.mailboxes = {"INBOX": {"118": "inbox invitation", "119": "inbox account"},
                          "Archive": {"118": "archive invitation", "119": "archive account"}}
        self.validity = validity
        self.atom_list = atom_list
        self.selected = None
        self.readonly = True

    def select(self, name, readonly=False):
        self.selected = shlex.split(name)[0]
        self.readonly = readonly
        return "OK", [b"2"]

    def response(self, name):
        return name, [self.validity] if self.validity else [None]

    def list(self):
        names = [name if self.atom_list and ' ' not in name else quote_mailbox(name)
                 for name in self.mailboxes]
        return "OK", [('(\\HasNoChildren) "/" ' + name).encode("ascii") for name in names]

    def create(self, name):
        if self.readonly:
            raise AssertionError("A dry run attempted to create a folder")
        name = shlex.split(name)[0]
        if name in self.mailboxes:
            return "NO", [b"already exists"]
        self.mailboxes[name] = {}
        return "OK", [b"created"]

    def subscribe(self, name):
        return "OK", [b"subscribed"]

    def uid(self, command, uid, target):
        if self.readonly:
            raise AssertionError("A dry run attempted to mutate mail")
        destination = self.mailboxes[shlex.split(target)[0]]
        source = self.mailboxes[self.selected]
        destination[str(len(destination) + 1)] = source[uid]
        if command == "move":
            del source[uid]
        return "OK", [b"completed"]


class MailboxEncodingTests(unittest.TestCase):
    def test_chinese_and_literal_ampersands(self):
        self.assertEqual(encode_mailbox("其他文件夹"), "&UXZO1mWHTvZZOQ-")
        self.assertEqual(encode_mailbox("A&B"), "A&-B")
        self.assertEqual(encode_mailbox("分拣&"), "&UgZi4w-&-")

    def test_quotes_are_escaped_and_names_with_controls_rejected(self):
        name = 'Sorted "mail" \\ work'
        self.assertEqual(shlex.split(quote_mailbox(encode_mailbox(name))), [name])
        with self.assertRaises(ValueError):
            encode_mailbox("INBOX\r\nLOGOUT")

    def test_list_accepts_atoms_and_escaped_quoted_names(self):
        self.assertEqual(list_mailbox_name(b'(\\HasNoChildren) "/" Sorted-mail'), "Sorted-mail")
        self.assertEqual(list_mailbox_name(b'(\\HasNoChildren) NIL "Sorted \\"mail\\""'), 'Sorted "mail"')


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.records = [record("118", "邀请"), record("119", "账号")]
        self.outcomes = [{"source_ref": message_ref(item.source_id, item.subject, item.body),
                          "status": "classified", "category": category}
                         for item, category in zip(self.records, ["other", "account-access"])]
        self.save()

    def save(self):
        (self.directory / "summary.json").write_text(json.dumps({"outcomes": self.outcomes}), encoding="utf-8")

    def test_matches_content_identity_and_category(self):
        plan = export_plan(self.directory, self.records)
        self.assertEqual([(item.uid, item.target) for item in plan],
                         [("118", "分拣-其他"), ("119", "分拣-账号访问")])

    def test_changed_content_and_failed_classifications_are_not_applied(self):
        self.records[0] = record("118", "邀请", "changed body")
        self.assertEqual([item.uid for item in export_plan(self.directory, self.records)], ["119"])
        self.outcomes[1]["status"] = "failed"
        self.save()
        with self.assertRaises(ApplyError):
            export_plan(self.directory, self.records)

    def test_log_subject_is_masked(self):
        item = record("118", "Contact user@example.com\x00")
        self.outcomes = [{"source_ref": message_ref(item.source_id, item.subject, item.body),
                          "status": "classified", "category": "other"}]
        self.save()
        label = export_plan(self.directory, [item])[0].subject_label
        self.assertNotIn("user@example.com", label)
        self.assertNotIn("\x00", label)
        self.assertIn("***@***", label)

    def test_nested_prefix_rejected(self):
        with self.assertRaises(ApplyError):
            export_plan(self.directory, self.records, folder_prefix="Sorted/Mail")


class ApplyTests(unittest.TestCase):
    def test_dry_run_leaves_mailbox_state_unchanged(self):
        connection = MailboxState()
        before = json.dumps(connection.mailboxes, sort_keys=True)
        result = apply_plan(connection, actions())
        self.assertEqual([item["status"] for item in result], ["planned", "planned"])
        self.assertEqual(json.dumps(connection.mailboxes, sort_keys=True), before)

    def test_copy_preserves_sources_and_populates_existing_atom_folders(self):
        connection = MailboxState(atom_list=True)
        connection.mailboxes["Triage-other"] = {}
        result = apply_plan(connection, actions(), dry_run=False)
        self.assertEqual([item["status"] for item in result], ["applied", "applied"])
        self.assertEqual(list(connection.mailboxes["Triage-other"].values()), ["inbox invitation"])
        self.assertEqual(list(connection.mailboxes["Triage-account"].values()), ["inbox account"])
        self.assertEqual(len(connection.mailboxes["INBOX"]), 2)

    def test_move_uses_requested_source_when_uids_overlap(self):
        connection = MailboxState()
        apply_plan(connection, actions(), mode="move", dry_run=False, mailbox="Archive")
        self.assertEqual(connection.mailboxes["Archive"], {})
        self.assertEqual(list(connection.mailboxes["Triage-other"].values()), ["archive invitation"])
        self.assertEqual(len(connection.mailboxes["INBOX"]), 2)

    def test_changed_or_missing_uidvalidity_aborts_before_mutation(self):
        for validity in [b"43", b"0", None]:
            with self.subTest(validity=validity):
                connection = MailboxState(validity=validity)
                before = json.dumps(connection.mailboxes, sort_keys=True)
                with self.assertRaises(ApplyError):
                    apply_plan(connection, actions(), dry_run=False)
                self.assertEqual(json.dumps(connection.mailboxes, sort_keys=True), before)

    def test_targets_with_spaces_quotes_backslashes_and_ampersands(self):
        connection = MailboxState()
        plan = actions()[:1]
        plan[0].target = 'Sorted "A&B" \\ mail'
        result = apply_plan(connection, plan, dry_run=False)
        self.assertEqual(result[0]["status"], "applied")
        wire_name = encode_mailbox(plan[0].target)
        self.assertEqual(list(connection.mailboxes[wire_name].values()), ["inbox invitation"])

    def test_one_failure_does_not_replay_or_block_later_messages(self):
        class Flaky(MailboxState):
            def uid(self, command, uid, target):
                if uid == "118":
                    raise OSError("connection result unknown")
                return super().uid(command, uid, target)
        connection = Flaky()
        result = apply_plan(connection, actions(), dry_run=False)
        self.assertEqual([item["status"] for item in result], ["failed", "applied"])
        self.assertEqual(connection.mailboxes["Triage-other"], {})
        self.assertEqual(list(connection.mailboxes["Triage-account"].values()), ["inbox account"])

    def test_invalid_mode_fails_before_mutating_mail(self):
        connection = MailboxState()
        with self.assertRaises(ApplyError):
            apply_plan(connection, actions(), mode="delete", dry_run=False)
        self.assertIsNone(connection.selected)


if __name__ == "__main__":
    unittest.main()
