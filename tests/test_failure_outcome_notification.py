# -*- coding: utf-8 -*-
"""How a message that fails says so: the toast and the outcome recorded on the message.

``BaseWorker.process_message`` runs here for real, in the test transaction (the registry is
in test mode, so the run cursor shares it), on an RPC processor that raises. The queue
operations that leave the transaction are replaced: ``terminate_message`` and
``imq.queue.send_notification``, whose arguments are what the tests read.

  - IMQError: title "Finished with failures", the error's message, cut and escaped; the
    outcome recorded in the response (``ended_with_failures``, ``failure_message``);
  - another exception: the title it always had and its class only, never its message; no outcome;
  - IMQRetryableError once the attempts are exhausted: unchanged.
"""

import logging
from unittest.mock import patch

import jsonpickle
from markupsafe import Markup

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.inouk_message_queue.api import IMQError, IMQRetryableError
from odoo.addons.inouk_message_queue.workers.base import BaseWorker, IMQ_TOAST_MAX_CHARS

MODULE = 'odoo.addons.inouk_message_queue.tests.test_failure_outcome_notification'

IMQ_ERROR_MESSAGE = "2 of 3 failed: <b>app-1</b> (disk full). Free space on the host, then launch the task again."


def _raise_imq_error(*args, **kwargs):
    raise IMQError(IMQ_ERROR_MESSAGE, results=[{'object': 'app-1'}])


def _raise_long_imq_error(*args, **kwargs):
    raise IMQError("x" * (IMQ_TOAST_MAX_CHARS * 3))


def _raise_empty_imq_error(*args, **kwargs):
    raise IMQError("")


def _raise_value_error(*args, **kwargs):
    raise ValueError("boom")


def _raise_secret_error(*args, **kwargs):
    raise RuntimeError("connection failed with password=s3cr3t-token-value")


def _raise_retryable(*args, **kwargs):
    raise IMQRetryableError("try later")


@tagged('post_install', '-at_install')
class TestFailureOutcomeNotification(TransactionCase):

    def setUp(self):
        super().setUp()
        self.queue = self.env.ref('inouk_message_queue.imq_queue__debug')
        self.worker = BaseWorker.__new__(BaseWorker)
        self.worker.logger = logging.getLogger('IMQ.test_failure_outcome')

    def _run(self, function, attempt=1, max_attempts=4):
        processor = self.env['imq.message_processor'].create({
            'type': 'rpc',
            'module': MODULE,
            'function': function,
            'is_method': False,
            'logging_activated': False,
            'capture_log': False,
            'notify_message_processing_fail': True,
        })
        message = self.env['imq.message'].create({
            'name': "A failing task",
            'queue_id': self.queue.id,
            'processor_id': processor.id,
            'user_id': self.env.user.id,
            'queue_message_id': 'test-%s' % function,
            'payload': jsonpickle.encode({'args': [], 'kwargs': {}}),
            'context': jsonpickle.encode({}),
            'attempt': attempt,
            'max_number_of_attempts': max_attempts,
        })
        with patch.object(BaseWorker, 'terminate_message', return_value=True), \
                patch.object(type(self.queue), 'send_notification') as send_notification:
            response = self.worker.process_message(self.env, message, object())
        calls = send_notification.call_args_list
        return response, calls, message

    def test_imq_error_says_it_finished_with_failures(self):
        response, calls, message = self._run('_raise_imq_error')
        self.assertEqual(response['state'], 'failed')
        self.assertTrue(response['ended_with_failures'])
        self.assertEqual(response['failure_message'], IMQ_ERROR_MESSAGE)
        self.assertEqual(len(calls), 1)
        args = calls[0].args
        self.assertEqual(args[0], 'danger')
        self.assertEqual(args[1], "Finished with failures")
        self.assertEqual(args[2], IMQ_ERROR_MESSAGE)
        self.assertNotIsInstance(args[2], Markup)

    def test_imq_error_text_is_escaped_in_the_toast_body(self):
        _, calls, message = self._run('_raise_imq_error')
        rendered = self.queue._build_notification_body(calls[0].args[2], message)
        self.assertIn("&lt;b&gt;app-1&lt;/b&gt;", rendered)
        self.assertNotIn("<b>app-1</b>", rendered)

    def test_long_imq_error_is_cut_with_a_pointer_to_the_message(self):
        response, calls, message = self._run('_raise_long_imq_error')
        text = calls[0].args[2]
        self.assertTrue(text.startswith("x" * IMQ_TOAST_MAX_CHARS))
        self.assertIn("see the message for the full text", text)
        self.assertLess(len(text), IMQ_TOAST_MAX_CHARS + 60)
        # the message keeps the whole text
        self.assertEqual(len(response['failure_message']), IMQ_TOAST_MAX_CHARS * 3)

    def test_empty_imq_error_keeps_title_and_points_to_the_message(self):
        response, calls, message = self._run('_raise_empty_imq_error')
        self.assertEqual(calls[0].args[1], "Finished with failures")
        self.assertEqual(calls[0].args[2], "See the message for the details.")
        self.assertTrue(response['ended_with_failures'])

    def test_other_exception_is_unchanged(self):
        response, calls, message = self._run('_raise_value_error')
        self.assertEqual(response['state'], 'failed')
        self.assertFalse(response['ended_with_failures'])
        self.assertFalse(response['failure_message'])
        args = calls[0].args
        self.assertEqual((args[0], args[1]), ('danger', "Processing failed"))
        self.assertEqual(args[2], "*ValueError* has been raised.")

    def test_other_exception_message_never_reaches_the_toast(self):
        response, calls, message = self._run('_raise_secret_error')
        for arg in calls[0].args:
            self.assertNotIn("s3cr3t-token-value", str(arg))
        self.assertEqual(calls[0].args[2], "*RuntimeError* has been raised.")

    def test_retryable_error_after_the_last_attempt_is_unchanged(self):
        response, calls, message = self._run('_raise_retryable', attempt=4, max_attempts=4)
        self.assertEqual(response['state'], 'failed')
        self.assertFalse(response['ended_with_failures'])
        args = calls[0].args
        self.assertEqual((args[0], args[1]), ('danger', "Retryable processing failed"))
        self.assertEqual(
            args[2], "*IMQRetryableError* has been raised 4 times (max attempt).")
