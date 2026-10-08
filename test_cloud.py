import copy
import unittest
import io
import json
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from datetime import date, datetime, timedelta
from urllib.error import URLError
from zoneinfo import ZoneInfo
from cloud_monitor import notify_new, target_id, ApiFailure, request_json
from calendar_scan import scan, CalendarTimeout, CalendarScanError, _scan_browser, PREVIOUS


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

    def test_spam_budget_is_not_reported_as_recipient_block(self):
        problem = {'code': 'message_rejected',
                   'message': 'This message was classified as spam; budget of 5 spam-flagged messages exceeded. Messages are blocked until reset.'}
        error = HTTPError('https://api.agentmail.to', 403, 'Forbidden', {},
                          io.BytesIO(json.dumps(problem).encode()))
        with patch('cloud_monitor.urlopen', side_effect=error):
            with self.assertRaises(ApiFailure) as caught:
                request_json('https://api.agentmail.to', 'test-token')
        self.assertEqual(caught.exception.reason, 'spam_budget_exceeded')


class CalendarWindowTest(unittest.TestCase):
    def run_calendar(self, first, end, previous_enabled=False, missing=None):
        start = date(2026, 10, 8)
        blocks = []
        day = first
        while day <= max(first, end):
            block = {day+timedelta(days=i): day+timedelta(days=i) == end for i in range(14)}
            if missing in block:
                del block[missing]
            blocks.append(block)
            day += timedelta(days=14)
        pw = MagicMock()
        browser = pw.return_value.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        page.goto.return_value.status = 200
        def button(role, name, exact):
            control = MagicMock()
            control.is_enabled.return_value = previous_enabled if name == PREVIOUS else True
            return control
        page.get_by_role.side_effect = button
        with patch('calendar_scan.parse_calendar', side_effect=blocks), patch('calendar_scan.time.sleep'):
            result = _scan_browser(start, end, False, pw)
        browser.close.assert_called_once()
        return result, page

    def test_disabled_previous_allows_official_start_and_keeps_end(self):
        end = date(2026, 11, 16)
        result, page = self.run_calendar(date(2026, 10, 11), end)
        self.assertEqual(result, ['2026-11-16'])
        self.assertEqual(page.wait_for_function.call_count, 2)

    def test_enabled_previous_does_not_allow_skipping_dates(self):
        with self.assertRaisesRegex(CalendarScanError, '向前翻页仍可用'):
            self.run_calendar(date(2026, 10, 11), date(2026, 11, 16), True)

    def test_earlier_calendar_still_uses_requested_start(self):
        result, _ = self.run_calendar(date(2026, 10, 4), date(2026, 10, 17), True)
        self.assertEqual(result, ['2026-10-17'])

    def test_missing_date_inside_effective_window_is_still_failure(self):
        with self.assertRaisesRegex(CalendarScanError, '未完整覆盖'):
            self.run_calendar(date(2026, 10, 11), date(2026, 11, 16), missing=date(2026, 10, 29))

    def test_official_start_after_end_returns_empty_and_closes_browser(self):
        result, page = self.run_calendar(date(2026, 10, 11), date(2026, 10, 10))
        self.assertEqual(result, [])
        page.wait_for_function.assert_not_called()


class CalendarRetryTest(unittest.TestCase):
    def test_timeout_restarts_entire_scan(self):
        with patch('calendar_scan._scan_once', side_effect=[CalendarTimeout(), ['2026-11-12']]) as attempt, patch('calendar_scan.time.sleep'):
            self.assertEqual(scan('start', 'end'), ['2026-11-12'])
            self.assertEqual(attempt.call_count, 2)

    def test_persistent_timeout_is_still_failure(self):
        with patch('calendar_scan._scan_once', side_effect=CalendarTimeout()) as attempt, patch('calendar_scan.time.sleep'):
            with self.assertRaises(CalendarTimeout):
                scan('start', 'end')
            self.assertEqual(attempt.call_count, 2)

    def test_invalid_calendar_is_not_retried_or_treated_as_empty(self):
        with patch('calendar_scan._scan_once', side_effect=ValueError('invalid calendar')) as attempt:
            with self.assertRaises(ValueError):
                scan('start', 'end')
            self.assertEqual(attempt.call_count, 1)


if __name__ == '__main__':
    unittest.main()
