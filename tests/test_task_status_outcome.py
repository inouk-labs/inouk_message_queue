# -*- coding: utf-8 -*-
"""``imq.message.get_task_status`` tells a task that finished with failures from one that stopped."""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTaskStatusOutcome(TransactionCase):

    def setUp(self):
        super().setUp()
        self.queue = self.env.ref('inouk_message_queue.imq_queue__debug')

    def _failed(self, **vals):
        return self.env['imq.message'].create(dict({
            'name': "A failed task",
            'queue_id': self.queue.id,
            'state': 'failed',
        }, **vals))

    def _status(self, message):
        return self.env['imq.message'].get_task_status([message.id])[0]

    def test_finished_with_failures(self):
        text = "1 of 2 applications failed (disk full on host-1). Free space, then launch the task again."
        status = self._status(self._failed(ended_with_failures=True, failure_message=text))
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['outcome'], 'finished_with_failures')
        self.assertEqual(status['failure_message'], text)
        self.assertIn("Do not deprovision", status['hint'])

    def test_stopped(self):
        status = self._status(self._failed())
        self.assertEqual(status['outcome'], 'stopped')
        self.assertNotIn('failure_message', status)
        self.assertNotIn("Do not deprovision", status['hint'])
        self.assertIn("Read imq.message_processing_log", status['hint'])

    def test_failure_message_is_cut(self):
        long_text = "y" * 5000
        status = self._status(self._failed(ended_with_failures=True, failure_message=long_text))
        self.assertTrue(status['failure_message'].startswith("y" * 2000))
        self.assertLess(len(status['failure_message']), 2100)

    def test_other_states_carry_no_outcome(self):
        message = self._failed()
        message.state = 'done'
        self.assertNotIn('outcome', self._status(message))
