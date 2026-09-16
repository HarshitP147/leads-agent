"""test_emails.py per QUALITY.md: regex + filters (drops junk, keeps real addresses).
No network — pure string/HTML processing."""

from __future__ import annotations

from enrich.discovery import _is_junk_email, find_emails

# NOT "example.com"/"example.org" — those are themselves in EMAIL_JUNK_SUBSTRINGS as
# the literal placeholder-domain check (see test_known_junk_domains_are_dropped below).
DOMAIN = "acme-corp.io"
SOURCE_URL = "https://acme-corp.io/contact"


def test_mailto_hrefs_are_harvested() -> None:
    html = '<a href="mailto:hello@acme-corp.io">Email us</a>'
    emails = find_emails(html, SOURCE_URL, DOMAIN)
    assert [e.email for e in emails] == ["hello@acme-corp.io"]
    assert emails[0].source_url == SOURCE_URL


def test_plain_text_email_is_harvested_if_same_site() -> None:
    html = "<p>Reach us at hello@acme-corp.io for support.</p>"
    emails = find_emails(html, SOURCE_URL, DOMAIN)
    assert [e.email for e in emails] == ["hello@acme-corp.io"]


def test_asset_fingerprint_email_is_dropped() -> None:
    # logo@2x.png-style asset references should never be treated as an email.
    assert _is_junk_email("logo@2x.png")
    html = '<img src="logo@2x.png"><p>logo@2x.png</p>'
    assert find_emails(html, SOURCE_URL, DOMAIN) == []


def test_known_junk_domains_are_dropped() -> None:
    assert _is_junk_email("noreply@sentry.io")
    assert _is_junk_email("hi@wixpress.com")
    assert _is_junk_email("test@example.com")  # the literal placeholder domain
    assert not _is_junk_email("hello@acme-corp.io")


def test_off_site_plain_text_email_dropped_unless_mailto() -> None:
    # A plain-text email for an unrelated domain isn't trusted unless it came from a
    # mailto: href (see discovery-and-cleaning.md's email regex post-filter).
    html = "<p>Contact partner at partner@unrelated-vendor.com</p>"
    assert find_emails(html, SOURCE_URL, DOMAIN) == []

    html_mailto = '<a href="mailto:partner@unrelated-vendor.com">Partner</a>'
    emails = find_emails(html_mailto, SOURCE_URL, DOMAIN)
    assert [e.email for e in emails] == ["partner@unrelated-vendor.com"]


def test_duplicate_emails_deduped_within_one_page() -> None:
    html = (
        '<a href="mailto:hello@acme-corp.io">Email</a>'
        "<p>Or write to hello@acme-corp.io directly.</p>"
    )
    emails = find_emails(html, SOURCE_URL, DOMAIN)
    assert len(emails) == 1
