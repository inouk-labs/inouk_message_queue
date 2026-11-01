"""Text rules of the Slack and Teams notifications.

Same rule as the Odoo toast (``inouk_notifications``): a ``str`` is text, a
:class:`markupsafe.Markup` is markup the caller means.

* Slack reads ``&``, ``<`` and ``>`` as control characters (``&amp;``,
  ``<!channel>``, ``<url|text>``): a ``str`` has those three escaped, nothing else,
  so the ``*bold*`` and ``_italic_`` a caller writes keep their effect. A ``Markup`` is
  HTML: its simple tags are converted to Slack's formatting, any other tag is dropped,
  and its text is escaped as Slack wants.
* Teams renders HTML: a ``str`` is HTML-escaped (quotes left alone, they are text in a
  card body), a ``Markup`` goes through unchanged.
"""
import html
from html.parser import HTMLParser

from markupsafe import Markup


class _HtmlToSlack(HTMLParser):
    """Converts the simple HTML a Markup may carry to Slack's mrkdwn."""

    _MARKS = {'b': '*', 'strong': '*', 'i': '_', 'em': '_', 's': '~', 'code': '`'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._href = []

    def handle_starttag(self, tag, attrs):
        if tag in self._MARKS:
            self.parts.append(self._MARKS[tag])
        elif tag in ('br', 'p', 'div'):
            self.parts.append('\n')
        elif tag == 'li':
            self.parts.append('\n- ')
        elif tag == 'a':
            href = dict(attrs).get('href')
            self._href.append(href)
            if href:
                self.parts.append('<%s|' % href.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def handle_endtag(self, tag):
        if tag in self._MARKS:
            self.parts.append(self._MARKS[tag])
        elif tag in ('p', 'div'):
            self.parts.append('\n')
        elif tag == 'a' and self._href:
            if self._href.pop():
                self.parts.append('>')

    def handle_data(self, data):
        self.parts.append(escape_slack(data))


def escape_slack(text):
    """The three characters Slack reads as control characters."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def slack_text(value):
    """``value`` as the text of a Slack message: ``str`` escaped, ``Markup`` converted."""
    if value is None:
        return ''
    if isinstance(value, Markup):
        parser = _HtmlToSlack()
        parser.feed(str(value))
        parser.close()
        return ''.join(parser.parts).strip('\n')
    return escape_slack(str(value))


def teams_html(value):
    """``value`` as the HTML of a Teams card: ``str`` escaped, ``Markup`` as it is."""
    if value is None:
        return Markup('')
    if isinstance(value, Markup):
        return value
    return Markup(html.escape(str(value), quote=False))
