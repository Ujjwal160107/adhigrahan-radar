"""Driving the e-Gazette *Search by Ministry* form.

This is the fragile module, and it is fragile by nature: the form is ASP.NET
WebForms with a cookieless session in the URL path, `__VIEWSTATE` and
`__EVENTVALIDATION` tokens that must be echoed back, AutoPostBack dropdowns
that each require their own round trip, and an image submit button. It works
today. It will break when the site is next rebuilt.

Everything is arranged so that when it does break, nothing else does:

* Reading a result page is `catalog.py`, which is pure and fixture-tested.
* Retrieving a document is `documents.py`, which needs no session at all.
* What has already been harvested is in `store.py`, on disk.

So a broken search degrades to "no new notifications found" - the build still
runs, on the corpus already mirrored. That is the whole reason discovery and
retrieval are separate modules.

There is no CAPTCHA here. The politeness limits in `http.py` apply.
"""
import re

from .. import config
from .catalog import read_page

_SESSION = re.compile(r"\(S\([a-z0-9]+\)\)")
_HIDDEN_INPUT = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)
_NAME = re.compile(r'name="([^"]+)"')
_VALUE = re.compile(r'value="([^"]*)"')
_CHECKED_RADIO = re.compile(r'id="rdb_Option_(\d)"[^>]*checked')

_DROPDOWNS = ("ddlMinistry", "ddlmonth", "ddlyear")


class SearchUnavailable(RuntimeError):
    """The search flow could not be driven. Callers should carry on with
    whatever is already in the store rather than failing the build."""


class MinistrySearch:
    """One e-Gazette session, reusable across many month queries."""

    def __init__(self, fetcher, base=config.EGAZETTE_BASE):
        self._fetcher = fetcher
        self._root = base
        self._base = None
        self._menu_url = None

    def months(self, ministry, year, months):
        """Yield every catalog entry for a ministry across the given months."""
        for month in months:
            yield from self.month(ministry, year, month)

    def month(self, ministry, year, month):
        """Every catalog entry for one ministry-month, following pagination.

        Stopping at the first page would silently harvest a fraction of the
        month: a busy MoRTH month runs to nine pages.
        """
        self._open()
        selection = {"ddlMinistry": ministry, "ddlyear": str(year), "ddlmonth": str(month)}
        url, html = self._submit(selection)
        first = read_page(html)
        yield from first

        # Pagination is read from the first page only. Every page repeats the
        # same page list, and re-reading it from each one risks looping if the
        # server ever renders it differently.
        for number in first.next_pages:
            url, html = self._turn_page(url, html, number, selection)
            yield from read_page(html)

    # ---- the ASP.NET dance -------------------------------------------------

    def _open(self):
        """Establish the session and reach the ministry form.

        The site hands out a cookieless session in the URL path and serves an
        error page to anything that skips the navigation order, so the two
        hops below are load-bearing rather than ceremonial.
        """
        if self._base:
            return
        landing = self._fetcher.get(self._root + "/")
        session = _SESSION.search(landing.url or "") or _SESSION.search(landing.text)
        if not session:
            raise SearchUnavailable("e-Gazette did not issue a session")
        self._base = f"{self._root}/{session.group(0)}/"

        menu = self._fetcher.get(self._base + "SearchMenu.aspx",
                                 referer=self._base + "default.aspx")
        form = _hidden_fields(menu.text)
        form["btnMinistry"] = "Search by Ministry"
        page = self._fetcher.post(self._base + "SearchMenu.aspx", form,
                                  referer=self._base + "SearchMenu.aspx")
        self._menu_url = page.url or (self._base + "SearchMinistry.aspx")
        self._form_page = page.text

    def _submit(self, selection):
        """Select ministry, year and month, then press the image submit.

        Each dropdown is AutoPostBack: it round-trips and reissues the
        ViewState, and a later selection posted against a stale token is
        rejected. They therefore have to be set one at a time, in order.
        """
        url, html = self._menu_url, self._form_page
        for name in _DROPDOWNS:
            url, html = self._postback(url, html, {"__EVENTTARGET": name}, selection)
        # An <input type="image"> posts the click coordinates, not a value.
        return self._postback(
            url, html, {"ImgSubmitDetails.x": "12", "ImgSubmitDetails.y": "12"}, selection)

    def _turn_page(self, url, html, number, selection):
        return self._postback(
            url, html,
            {"__EVENTTARGET": "gvGazetteList", "__EVENTARGUMENT": f"Page${number}"},
            selection)

    def _postback(self, url, html, action, selection):
        """One round trip: echo the page's tokens back, apply `action`, and
        return the URL landed on plus the page that came out."""
        form = _hidden_fields(html)
        form["__EVENTTARGET"] = ""
        form["__EVENTARGUMENT"] = ""
        form["rdb_Option"] = _selected_radio(html)
        form.update(selection)
        form.update(action)
        response = self._fetcher.post(url, form, referer=url)
        return (response.url or url), response.text


def _hidden_fields(html):
    """Every hidden input on the page - `__VIEWSTATE`, `__EVENTVALIDATION`
    and friends. ASP.NET rejects a postback that does not echo them back
    exactly, so they are copied wholesale rather than named individually."""
    fields = {}
    for tag in _HIDDEN_INPUT.findall(html):
        name = _NAME.search(tag)
        if name:
            value = _VALUE.search(tag)
            fields[name.group(1)] = value.group(1) if value else ""
    return fields


def _selected_radio(html):
    m = _CHECKED_RADIO.search(html)
    return m.group(1) if m else "0"
