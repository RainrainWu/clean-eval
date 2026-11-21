"""What may leave the CI runner. Agent text and tool output can carry secrets (read from the environment or the checkout)
and hidden instructions, so everything posted or written out is cleaned first: values of secret-looking environment
variables and their base64 forms, well-known token formats and authorization headers become [REDACTED], and HTML
comments are defanged, so nothing hidden or marker-like reaches GitHub."""
import base64
import re

REDACTED = "[REDACTED]"
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.I)
TOKEN_PATTERNS = [re.compile(p) for p in (
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{40,}", r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", r"xox[abpr]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")]
AUTH_HEADER = re.compile(r"(?i)(authorization:\s*(?:basic|bearer|token)\s+)[A-Za-z0-9+/=._\-]{8,}")
MARKER_RE = re.compile(r"<!--\s*clean-eval:[^>]*-->")
MAX_REPORT_CHARS = 60_000  # GitHub caps review bodies at 65,536


def secret_values(environ):
    """The values to redact, from an environment mapping: every secret-looking variable's value (8+ characters) and its
    base64 forms, e.g. the `AUTHORIZATION: basic <b64("x-access-token:<token>")>` actions/checkout persists in .git/config."""
    values = {v for k, v in environ.items() if SECRET_NAME.search(k) and v and len(v) >= 8}
    encoded = {base64.b64encode(raw.encode()).decode() for v in values for raw in (v, "x-access-token:" + v)}
    return values | encoded


def neutralize_comments(text):
    """Agent text may not carry HTML comments: strip clean-eval markers until none reassemble from nested input, then
    defang every remaining `<!--`, so nothing hidden (or marker-like) reaches GitHub."""
    while True:
        stripped = MARKER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.replace("<!--", "&lt;!--")


class Redactor:
    def __init__(self, secrets=()):
        # longest first, so a secret that contains another is replaced whole
        self.secrets = sorted(set(secrets), key=len, reverse=True)

    def redact(self, text):
        """(text with secrets replaced, number of replacements)."""
        n = 0
        for v in self.secrets:
            if v in text:
                n += text.count(v)
                text = text.replace(v, REDACTED)
        for pattern in TOKEN_PATTERNS:
            text, k = pattern.subn(REDACTED, text)
            n += k
        text, k = AUTH_HEADER.subn(r"\1" + REDACTED, text)
        return text, n + k

    def sanitize_report(self, text):
        """(report safe to post, redactions): no comments (the agent can't forge or duplicate the marker), no secrets,
        within GitHub's size limit."""
        text, n = self.redact(neutralize_comments(text or ""))
        if len(text) > MAX_REPORT_CHARS:
            text = text[:MAX_REPORT_CHARS] + "\n\n_[report truncated]_\n"
        return text.strip() + "\n", n
