# -*- coding: utf-8 -*-
"""Slack and Teams notifications follow the rule of the Odoo toast: a ``str`` is text and
is escaped for the channel, a ``Markup`` is the markup the caller means.

The HTTP sending is simulated: Slack's ``requests.post`` and Teams' ``send_teams_message``
(and, for the card itself, ``pymsteams.connectorcard``).
"""
import json
import logging
from unittest.mock import MagicMock, patch

import pymsteams
from markupsafe import Markup

from odoo.tests.common import TransactionCase, tagged

_logger = logging.getLogger(__name__)

NASTY = "<b>x</b> <!channel> & more"

# The texts the callers of send_notification() send today (Slack mrkdwn: ``*bold*`` and
# quotes, no HTML): host__metrics, pg_dump, postgresql_sr, lxc_image, muppy_postgresql_ha
# (pg_cluster, pg_replicated_cluster_set, pglookout) and the workers of IMQ itself.
CALLER_TEXTS = [
    ("Disk left space danger", "Partition '/dev/sda1' usage: 91.20% on host 'web-1' is above 90.0%."),
    ("PostgreSQL Backup Error", "pg_dump of database 'db1' on cluster pg-main failed!"),
    ("PostgreSQL Replication", "RCS max_wal_senders is too low (3 vs 5 standbys). Please plan to increase it !"),
    ("New Muppy version available", "Muppy Server *18.99.0* is available. You're running version *18.98.0*."),
    ("PostgreSQL Cluster 'pg-main' replication lag", "Cluster pg-main is 12 seconds behind primary !!"),
    ("PostgreSQL HA", "Cluster 'pg-main'/4 notified:'FAILOVER_LAUNCHED' with replication_time_lag=3 and "
                      "min_replication_time_lag=n/a and RCS::HA Deactivated=False."),
    ("PostgreSQL HA reconnect", "pglookout daemon of cluster *'pg-main'* is reachable again (Connexion cut lasted 0:01:02)."),
    ("Processing failed", "*ValueError* has been raised."),
    ("Processing done without error", "Duration=0hours0min4s"),
]


@tagged('post_install', '-at_install')
class TestSlackTeamsEscaping(TransactionCase):

    def setUp(self):
        super().setUp()
        self.queue = self.env.ref('inouk_message_queue.imq_queue__debug')
        self.queue.write({
            'slack_webhook_url': 'https://hooks.slack.invalid/x',
            'use_msteams_notifications': True,
            'msteams_webhookurl': 'https://teams.invalid/x',
            'use_odoo_notifications': False,
        })
        self.message = self.env['imq.message'].create({
            'name': "Create <i>DB</i> & <!here>",
            'queue_id': self.queue.id,
            'user_id': self.env.user.id,
        })

    def _slack(self, title, message, message_obj=None):
        with patch('odoo.addons.inouk_message_queue.models.queue__slack.requests.post') as post:
            self.queue.send_slack_notification('info', title, message, message_obj=message_obj)
        return json.loads(post.call_args.kwargs['data'])['text']

    def _teams(self, message_type, title, message, message_obj=None):
        """What send_notification() hands to the Teams card."""
        with patch.object(type(self.queue), 'send_teams_message') as send, \
                patch.object(type(self.queue), 'send_slack_notification'), \
                patch.object(type(self.queue), 'send_odoo_notification'):
            self.queue.send_notification(message_type, title, message, message_obj=message_obj)
        return send.call_args.kwargs['message']

    # Slack
    def test_slack_str_is_escaped(self):
        text = self._slack("T", NASTY)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &lt;!channel&gt; &amp; more", text)
        self.assertNotIn("<!channel>", text)

    def test_slack_title_and_message_name_are_escaped(self):
        text = self._slack("<!channel> alert", "ok", message_obj=self.message)
        self.assertTrue(text.startswith("*&lt;!channel&gt; alert*"))
        self.assertNotIn("<!here>", text)
        self.assertIn("&lt;i&gt;DB&lt;/i&gt; &amp; &lt;!here&gt;", text)

    def test_slack_mrkdwn_of_a_str_is_kept(self):
        self.assertIn("*bold* and _it_", self._slack("T", "*bold* and _it_"))

    def test_slack_markup_is_converted(self):
        text = self._slack("T", Markup('<b>x</b><br/>a &amp; <a href="https://e.org/?a=1&amp;b=2">l</a> <u>u</u>'))
        self.assertEqual(text, "*T*\n*x*\na &amp; <https://e.org/?a=1&amp;b=2|l> u")

    def test_slack_markup_text_is_escaped_for_slack(self):
        self.assertEqual(self._slack("T", Markup("1 &lt; 2 &amp; <i>3</i>")), "*T*\n1 &lt; 2 &amp; _3_")

    # Teams
    def test_teams_str_is_escaped(self):
        message = self._teams('info', "T", NASTY)
        self.assertEqual(str(message), "&lt;b&gt;x&lt;/b&gt; &lt;!channel&gt; &amp; more")

    def test_teams_markup_is_html(self):
        message = self._teams('info', "T", Markup("<b>x</b> &amp; y"))
        self.assertEqual(str(message), "<b>x</b> &amp; y")

    def test_teams_message_name_is_escaped(self):
        message = self._teams('info', "T", NASTY, message_obj=self.message)
        self.assertEqual(
            str(message),
            "Create &lt;i&gt;DB&lt;/i&gt; &amp; &lt;!here&gt;<br/>"
            "&lt;b&gt;x&lt;/b&gt; &lt;!channel&gt; &amp; more")

    def test_teams_card_carries_the_text(self):
        """The card's activityTitle is the escaped text, through the real pymsteams section."""
        card = MagicMock()
        with patch.object(pymsteams, 'connectorcard', return_value=card):
            self.queue.send_teams_notification('info', "T", NASTY)
        section = card.addSection.call_args.args[0]
        self.assertEqual(section.payload['activityTitle'], "&lt;b&gt;x&lt;/b&gt; &lt;!channel&gt; &amp; more")
        self.assertTrue(card.send.called)

    def test_teams_direct_call_markup_is_html(self):
        card = MagicMock()
        with patch.object(pymsteams, 'connectorcard', return_value=card):
            self.queue.send_teams_notification('info', "T", Markup("<b>x</b>"))
        self.assertEqual(card.addSection.call_args.args[0].payload['activityTitle'], "<b>x</b>")

    def test_empty_text_is_unchanged(self):
        self.assertEqual(self._slack("T", ""), "*T*")
        self.assertFalse(self._teams('info', "T", ""))
        self.assertEqual(str(self._teams('info', "T", "", message_obj=self.message)),
                         "Create &lt;i&gt;DB&lt;/i&gt; &amp; &lt;!here&gt;<br/>")

    # Failures land in the task's journal
    def test_failed_posts_are_logged_to_the_task_logger(self):
        task_logger = MagicMock()
        with patch('odoo.addons.inouk_message_queue.models.queue__slack.requests.post', side_effect=OSError("boom")), \
                patch.object(pymsteams, 'connectorcard', return_value=MagicMock(send=MagicMock(side_effect=OSError("boom")))):
            self.queue.send_notification('info', "T", "m", _imq_logger=task_logger)
        errors = [c.args[0] for c in task_logger.error.call_args_list]
        self.assertTrue(any("slack" in e for e in errors), errors)
        self.assertTrue(any("Teams" in e for e in errors), errors)

    # Callers: same body as before
    def test_callers_send_the_same_body_as_before(self):
        plain = self.env['imq.message'].create({
            'name': "Backup db1", 'queue_id': self.queue.id, 'user_id': self.env.user.id})
        for title, msg in CALLER_TEXTS:
            before_slack = f"*{title}*\n{msg}"
            after_slack = self._slack(title, msg)
            self.assertEqual(after_slack, before_slack, title)
            before_teams = "%s<br/>%s" % (plain.name, msg)
            after_teams = str(self._teams('info', title, msg, message_obj=plain))
            self.assertEqual(after_teams, before_teams, title)
            # no message_obj: the text alone
            self.assertEqual(str(self._teams('info', title, msg)), msg, title)
            _logger.debug("L13 BEFORE=AFTER | Slack: %r | Teams: %r", after_slack, after_teams)
