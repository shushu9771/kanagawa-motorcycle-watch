import copy
import unittest
import io
import json
from unittest.mock import patch
from urllib.error import HTTPError
from datetime import datetime
from urllib.error import URLError
from zoneinfo import ZoneInfo
from cloud_monitor import notify_new, target_id, ApiFailure, request_json


DEST = [('email', 'a@example.com'), ('email', 'b@example.com'), ('line', 'test-user')]
NOW = datetime(2026, 10, 5, 19, 0, tzinfo=ZoneInfo('Asia/Tokyo'))


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        self.state = {'version': 1, 'deliveries': {}}
        self.snapshots = []
        self.sends = []

    def persist(self, state):
        self.snapshots.append(copy.deepcopy(state))

    def send(self, kind, recipient, body, retry):
        self.sends.append((kind, recipient, body, retry))

    def run_check(self, available, sender=None):
        return notify_new(self.state, available, DEST, self.persist, sender or self.send, NOW)

    def test_all_three_destinations_dates_batched(self):
        self.assertTrue(self.run_check(['2026-11-12', '2026-11-16']))
        self.assertEqual(len(self.sends), 3)
        for send in self.sends:
            self.assertIn('2026-11-12', send[2])
            self.assertIn('2026-11-16', send[2])
        for snapshot in self.snapshots:
            self.assertNotIn('a@example.com', str(snapshot))

    def test_permanent_dedup_after_disappearance_and_restart(self):
        self.run_check(['2026-11-16'])
        self.state = copy.deepcopy(self.snapshots[-1])
        self.run_check([])
        self.run_check(['2026-11-16'])
        self.assertEqual(len(self.sends), 3)

    def test_rejected_recipient_does_not_block_other_channels(self):
        def sender(kind, recipient, body, retry):
            if recipient == 'b@example.com':
                raise ApiFailure(401)
            self.send(kind, recipient, body, retry)
        self.assertFalse(self.run_check(['2026-11-16'], sender))
        self.assertEqual(len(self.sends), 2)
        self.assertTrue(self.run_check(['2026-11-16']))
        self.assertEqual(self.sends[-1][1], 'b@example.com')
        self.assertEqual(len(self.sends), 3)

    def test_ambiguous_email_send_is_not_duplicated(self):
        def sender(kind, recipient, body, retry):
            if recipient == 'a@example.com':
                raise URLError('timeout')
            self.send(kind, recipient, body, retry)
        self.assertFalse(self.run_check(['2026-11-16'], sender))
        self.assertFalse(self.run_check(['2026-11-16']))
        self.assertEqual(len(self.sends), 2)
        entry = self.state['deliveries'][target_id('email', 'a@example.com')][0]
        self.assertEqual(entry['status'], 'uncertain')

    def test_line_retry_uses_same_key_and_payload(self):
        attempted = []
        def timeout(kind, recipient, body, retry):
            if kind == 'line':
                attempted.append((body, retry))
                raise URLError('timeout')
            self.send(kind, recipient, body, retry)
        self.assertFalse(self.run_check(['2026-11-16'], timeout))
        def retry(kind, recipient, body, key):
            if kind == 'line':
                attempted.append((body, key))
            self.send(kind, recipient, body, key)
        self.assertTrue(self.run_check(['2026-11-16'], retry))
        self.assertEqual(attempted[0], attempted[1])

    def test_provider_diagnostic_redacts_private_values(self):
        problem = {'code': 'message_rejected', 'message': 'Recipient blocked',
                   'fix': 'Remove private@example.com at https://example.com/private?token=secret-value using am_test-secret'}
        error = HTTPError('https://api.agentmail.to', 403, 'Forbidden', {},
                          io.BytesIO(json.dumps(problem).encode()))
        with patch('cloud_monitor.urlopen', side_effect=error):
            with self.assertRaises(ApiFailure) as caught:
                request_json('https://api.agentmail.to', 'am_test-secret')
        self.assertEqual(caught.exception.reason, 'recipient_blocked')
        for private in ('private@example.com', 'secret-value', 'am_test-secret'):
            self.assertNotIn(private, caught.exception.detail)


if __name__ == '__main__':
    unittest.main()
