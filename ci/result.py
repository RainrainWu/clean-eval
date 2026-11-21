#!/usr/bin/env python3
"""Deterministic half of the clean-eval action.

The CI agent only *proposes* (agent/report.md + agent/result.json). This script turns that into
what CI trusts:

  finalize --skill pr-review   validate findings; unknown rule IDs become NEW; each finding's severity
                               is floored at its rule's severity in the rules table; verdict/blockers are
                               derived from the findings and tier/score come from the precomputed risk
  finalize --skill eval        validate; with --apply, accept edits to .clean-eval/ only if they are
                               well-formed, touch only tunable keys, and don't lower AUC on replay —
                               otherwise restore the original file
  finalize --skill risk-heat-map   sanitize the optional narrative
  post                         post one review (inline comments attached, with fallbacks) and dismiss
                               this reviewer's earlier "changes requested" reviews
  redact-file                  scrub secrets from a file in place (e.g. the agent transcript)

Everything that leaves the runner (reports, review bodies, step outputs) is redacted: values of
secret-looking environment variables and well-known token formats are replaced with [REDACTED].
Step outputs are built only from enums and numbers, so no agent text reaches $GITHUB_OUTPUT.
"""
import argparse
import base64
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(os.path.dirname(HERE), ".claude", "skills")
sys.path.insert(0, os.path.join(SKILLS, "risk-heat-map", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "eval", "scripts"))
import risk  # noqa: E402
import history  # noqa: E402

RULE_RE = re.compile(r"^([A-Z]{1,4}-\d{2,3}|NEW)$")
RULE_ROW = re.compile(r"^\|\s*([A-Z]{1,4}-\d{2,3})\s*\|\s*(blocker|major|minor)\s*\|\s*(Low|Medium|High|Critical)\s*\|",
                      re.M)
SEVERITIES = ("minor", "major", "blocker")  # ascending
TIERS = tuple(risk.TIER_ORDER)
MARKER_RE = re.compile(r"<!--\s*clean-eval:[^>]*-->")
MAX_REPORT_CHARS = 60_000  # GitHub caps review bodies at 65,536
MAX_FINDINGS = 50
TUNABLE_KEYS = {"likelihood_weights", "impact_weights", "level_thresholds", "criticality",
                "criticality_default", "new_module_tier", "version"}

SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.I)
TOKEN_PATTERNS = [re.compile(p) for p in (
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{40,}", r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", r"xox[abpr]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")]
AUTH_HEADER = re.compile(r"(?i)(authorization:\s*(?:basic|bearer|token)\s+)[A-Za-z0-9+/=._\-]{8,}")
KNOWN_CLEAN_EVAL_FILES = {"risk-model.json", "review-rules.md"}
MIN_PRS, MIN_DEFECTIVE = 20, 5  # below this, a model edit has no evidence behind it
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class Invalid(Exception):
    pass


# ---------------------------------------------------------------- redaction

def _secret_values():
    vals = {v for k, v in os.environ.items() if SECRET_NAME.search(k) and v and len(v) >= 8}
    # also their base64 forms, e.g. the `AUTHORIZATION: basic <b64("x-access-token:<token>")>` that
    # actions/checkout persists in .git/config
    encoded = {base64.b64encode(raw.encode()).decode() for v in vals for raw in (v, f"x-access-token:{v}")}
    return sorted(vals | encoded, key=len, reverse=True)


def redact(text):
    """Return (text with secrets replaced, number of replacements)."""
    n = 0
    for v in _secret_values():
        if v in text:
            n += text.count(v)
            text = text.replace(v, "[REDACTED]")
    for pat in TOKEN_PATTERNS:
        text, k = pat.subn("[REDACTED]", text)
        n += k
    text, k = AUTH_HEADER.subn(r"\1[REDACTED]", text)
    return text, n + k


def neutralize_comments(text):
    """Agent text may not carry HTML comments: strip clean-eval markers until none reassemble from nested
    input, then defang every remaining `<!--` so nothing hidden (or marker-like) reaches GitHub."""
    while True:
        stripped = MARKER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.replace("<!--", "&lt;!--")


def sanitize_report(text):
    text = neutralize_comments(text or "")  # the agent can't forge or duplicate the marker
    text, n = redact(text)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n\n_[report truncated]_\n"
    return text.strip() + "\n", n


# ---------------------------------------------------------------- validation

def _str(v, field, maxlen, one_line=False):
    if not isinstance(v, str):
        raise Invalid(f"{field} must be a string")
    if one_line and ("\n" in v or "\r" in v):
        raise Invalid(f"{field} must be a single line")
    return v[:maxlen]


def parse_rules(text):
    """{rule_id: {"severity", "min_tier"}} from the rules tables."""
    return {m.group(1): {"severity": m.group(2), "min_tier": m.group(3)} for m in RULE_ROW.finditer(text)}


def validate_pr_review(raw, risk_ctx, rules):
    if not isinstance(raw, dict):
        raise Invalid("result.json must be an object")
    items = raw.get("findings") or []
    if not isinstance(items, list):
        raise Invalid("findings must be a list")
    findings, redactions = [], 0
    for i, f in enumerate(items[:MAX_FINDINGS]):
        if not isinstance(f, dict):
            raise Invalid(f"findings[{i}] must be an object")
        rule = _str(f.get("rule", ""), f"findings[{i}].rule", 20, one_line=True)
        if not RULE_RE.match(rule):
            raise Invalid(f"findings[{i}].rule {rule!r} is not a rule ID or NEW")
        sev = f.get("severity")
        if sev not in SEVERITIES:
            raise Invalid(f"findings[{i}].severity must be one of {SEVERITIES}")
        line = f.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise Invalid(f"findings[{i}].line must be a positive integer or null")
        file_ = f.get("file")
        file_ = _str(file_, f"findings[{i}].file", 500, one_line=True) if file_ else None
        summary = _str(f.get("summary", ""), f"findings[{i}].summary", 1500)
        fix = _str(f.get("fix") or "", f"findings[{i}].fix", 2500)
        notes = []
        if rule != "NEW" and rule not in rules:
            notes.append(f"cited unknown rule {rule}")
            rule = "NEW"
        if rule in rules and SEVERITIES.index(sev) < SEVERITIES.index(rules[rule]["severity"]):
            notes.append(f"severity raised from {sev} to the rule's {rules[rule]['severity']}")
            sev = rules[rule]["severity"]
        summary, n1 = redact(neutralize_comments(summary))
        fix, n2 = redact(neutralize_comments(fix))
        redactions += n1 + n2
        findings.append({"rule": rule, "severity": sev, "file": file_, "line": line,
                         "summary": summary, "fix": fix, "notes": notes})
    blockers = sum(f["severity"] == "blocker" for f in findings)
    majors = sum(f["severity"] == "major" for f in findings)
    tier = risk_ctx.get("max_tier")
    if tier not in TIERS:
        raise Invalid("risk.json has no valid max_tier")
    return {
        "pr": risk_ctx.get("pr"), "base": risk_ctx.get("base"), "head": risk_ctx.get("head"),
        # gate-relevant fields are derived, never taken from agent text
        "verdict": "request_changes" if blockers else ("comment" if majors else "approve"),
        "max_tier": tier, "max_score": float(risk_ctx.get("max_score", 0.0)),
        "blockers": blockers, "majors": majors,
        "rules_version": risk_ctx.get("rules_version"), "model_version": risk_ctx.get("model_version"),
        "findings": findings, "redactions": redactions,
    }


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_model_change(new, orig):
    """Raise Invalid unless `new` is a well-formed model differing from `orig` only in tunable keys."""
    if not isinstance(new, dict):
        raise Invalid("risk-model.json must be an object")
    changed = {k for k in set(new) | set(orig) if new.get(k) != orig.get(k)}
    if changed - TUNABLE_KEYS:
        raise Invalid(f"only tunable keys may change in CI ({sorted(TUNABLE_KEYS)}); also changed: {sorted(changed - TUNABLE_KEYS)}")
    for axis in ("likelihood_weights", "impact_weights"):
        w = new.get(axis)
        if not isinstance(w, dict) or set(w) != set(orig[axis]):
            raise Invalid(f"{axis} must keep the signals {sorted(orig[axis])}")
        if any(not _num(v) or v < 0 for v in w.values()) or sum(w.values()) <= 0:
            raise Invalid(f"{axis} must be non-negative numbers with a positive sum")
    th = new.get("level_thresholds") or {}
    if not all(_num(th.get(k)) for k in ("medium", "high")) or not 0 < th["medium"] < th["high"] < 1:
        raise Invalid("level_thresholds must satisfy 0 < medium < high < 1")
    crit = new.get("criticality", {})
    if not isinstance(crit, dict) or any(not _num(v) or not 0 <= v <= 1 for v in crit.values()):
        raise Invalid("criticality values must be numbers in [0, 1]")
    cd = new.get("criticality_default", 0.5)
    if not _num(cd) or not 0 <= cd <= 1:
        raise Invalid("criticality_default must be a number in [0, 1]")
    if new.get("new_module_tier", "Medium") not in TIERS:
        raise Invalid("new_module_tier must be a tier")
    if "version" in new and (not isinstance(new["version"], int) or isinstance(new["version"], bool)):
        raise Invalid("version must be an integer")
    return changed


def validate_rules_change(text):
    rules = parse_rules(text)
    ids = RULE_ROW.findall(text)
    if not rules:
        raise Invalid("review-rules.md has no rule rows")
    if len(ids) != len(rules):
        raise Invalid("review-rules.md has duplicate rule IDs")
    return rules


# ---------------------------------------------------------------- finalize

def write_outputs(path, outputs):
    with open(path, "a") as o:
        for k, v in outputs.items():
            o.write(f"{k}={v}\n")  # values are enums/numbers built here: no newlines possible


def _restore(path, orig_text, existed):
    if existed:
        with open(path, "w") as f:
            f.write(orig_text)
    elif os.path.exists(path):
        os.remove(path)


def _revert_other_files(ws, ctx):
    """Restore every .clean-eval/ file other than the two validated ones to its pre-run state."""
    live, orig = os.path.join(ws, ".clean-eval"), os.path.join(ctx, "clean-eval.orig")
    reverted = set()
    for top, restore in ((live, False), (orig, True)):
        for root, _, names in os.walk(top) if os.path.isdir(top) else ():
            for name in names:
                rel = os.path.relpath(os.path.join(root, name), top)
                if rel in KNOWN_CLEAN_EVAL_FILES:
                    continue
                p, o = os.path.join(live, rel), os.path.join(orig, rel)
                if not restore and not os.path.exists(o):
                    os.remove(p)                                      # created by the agent
                elif not os.path.exists(p) or not filecmp.cmp(p, o, shallow=False):
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    shutil.copyfile(o, p)                             # changed or removed by the agent
                else:
                    continue
                reverted.add(rel)
    return sorted(reverted)


def _check_clean_eval_edits(a, hist, context):
    """Accept or revert the agent's edits to .clean-eval/.

    Returns (model_change, rules_change, other_files_reverted, revert_all)."""
    ws, ctx = a.workspace, a.ctx
    model_path = os.path.join(ws, risk.REPO_MODEL)
    orig_model_text = risk.read_text(os.path.join(ctx, "risk-model.orig.json"))
    rules_path = os.path.join(ws, ".clean-eval", "review-rules.md")
    orig_rules = risk.read_text(os.path.join(ctx, "review-rules.orig.md"))
    revert_model = lambda: _restore(model_path, orig_model_text, context.get("model_existed"))  # noqa: E731
    revert_rules = lambda: _restore(rules_path, orig_rules, context.get("rules_existed"))  # noqa: E731

    model_change = rules_change = None
    if os.path.exists(model_path) and risk.read_text(model_path) != orig_model_text:
        try:
            if not a.apply:
                raise Invalid("apply=false")
            new = risk.read_json(model_path)
            orig = json.loads(orig_model_text)
            changed = validate_model_change(new, orig)
            prs, defective = hist["totals"]["prs"], hist["totals"]["defective_prs"]
            if prs < MIN_PRS or defective < MIN_DEFECTIVE:
                raise Invalid(f"sample too small for a model change ({prs} PRs, {defective} defective; "
                              f"need at least {MIN_PRS} and {MIN_DEFECTIVE})")
            # compare replay with replay: stored signals are rounded, so a replay of the unchanged
            # model can differ slightly from the collected AUC
            before = history.replay(hist, orig)["auc_max_score"]
            after = history.replay(hist, new)["auc_max_score"]
            if before is None or after is None:
                raise Invalid("AUC is undefined for this period (no defective or no clean PRs): no evidence for a change")
            if after < before:
                raise Invalid(f"replay AUC {after} is below the current model's {before}")
            model_change = {"accepted": True, "changed_keys": sorted(changed), "auc_before": before, "auc_after": after}
        except (Invalid, ValueError, KeyError, TypeError, AttributeError) as e:
            revert_model()
            model_change = {"accepted": False, "reason": str(e)}
    if os.path.exists(rules_path) and risk.read_text(rules_path) != orig_rules:
        try:
            if not a.apply:
                raise Invalid("apply=false")
            validate_rules_change(risk.read_text(rules_path))
            rules_change = {"accepted": True}
        except Invalid as e:
            revert_rules()
            rules_change = {"accepted": False, "reason": str(e)}

    other = _revert_other_files(ws, ctx)

    def revert_all():
        revert_model()
        revert_rules()
    return model_change, rules_change, other, revert_all


def finalize_eval(a):
    hist = risk.read_json(os.path.join(a.ctx, "eval-history.json"))
    context = risk.read_json(os.path.join(a.ctx, "context.json"))
    # Check edits before reading the agent's output: an invalid result must not leave unchecked edits behind.
    model_change, rules_change, other_reverted, revert_all = _check_clean_eval_edits(a, hist, context)
    try:
        raw = risk.read_json(os.path.join(a.agent, "result.json"))
        if not isinstance(raw, dict):
            raise Invalid("result.json must be an object")
        recs = raw.get("recommendations") or []
        if not isinstance(recs, list):
            raise Invalid("recommendations must be a list")
        recommendations = [{k: redact(_str(str(x.get(k, "")), f"recommendations.{k}", 2000))[0]
                            for k in ("target", "change", "evidence")} for x in recs[:30] if isinstance(x, dict)]
    except (Invalid, OSError, ValueError) as e:
        revert_all()
        raise Invalid(f"{e} (any .clean-eval/ edits were reverted)")

    baseline = hist["calibration"]["auc_max_score"]
    res = {"since": hist["period"]["since"], "until": hist["period"]["until"],
           "prs": hist["totals"]["prs"], "defective_prs": hist["totals"]["defective_prs"], "auc": baseline,
           "recommendations": recommendations, "model_change": model_change, "rules_change": rules_change,
           "other_files_reverted": other_reverted}
    res["applied"] = any((c or {}).get("accepted") for c in (model_change, rules_change))
    return res, {"auc": "" if baseline is None else f"{baseline:.3f}", "applied": "true" if res["applied"] else "false"}


def finalize(a):
    report_src = os.path.join(a.agent, "report.md")
    try:
        if a.skill == "pr-review":
            raw = risk.read_json(os.path.join(a.agent, "result.json"))
            risk_ctx = risk.read_json(os.path.join(a.ctx, "risk.json"))
            rules = parse_rules(risk.read_text(os.path.join(a.ctx, "review-rules.md")))
            res = validate_pr_review(raw, risk_ctx, rules)
            outputs = {"verdict": res["verdict"], "max-tier": res["max_tier"],
                       "max-score": f"{res['max_score']:.3f}", "blockers": str(res["blockers"])}
        elif a.skill == "eval":
            res, outputs = finalize_eval(a)
        else:
            res, outputs = None, {}
            if not os.path.exists(report_src):
                report_src = os.path.join(a.out, "heatmap.md")
    except (Invalid, OSError, ValueError, KeyError, TypeError) as e:
        print(f"::error::clean-eval: invalid agent output: {e}", file=sys.stderr)
        return 2

    report, n = sanitize_report(risk.read_text(report_src) if os.path.exists(report_src) else "_no report written_")
    risk.write_text(os.path.join(a.out, "report.md"), report)
    if res is not None:
        res["redactions"] = res.get("redactions", 0) + n
        risk.write_json(os.path.join(a.out, "result.json"), res, indent=2)
        if res["redactions"]:
            print(f"::warning::clean-eval redacted {res['redactions']} secret-looking value(s) from agent output",
                  file=sys.stderr)
    write_outputs(a.gh_output, outputs)
    print(json.dumps(outputs))
    return 0


# ---------------------------------------------------------------- posting

def gh(*args, stdin=None):
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    return r.returncode == 0, r.stdout, (r.stderr or r.stdout).strip()


def commentable_lines(ctx):
    """{path: {new-side line numbers GitHub accepts inline comments on}} from the per-file patches."""
    index_path = os.path.join(ctx, "diff", "index.json") if ctx else ""
    if not os.path.exists(index_path):
        return None
    out = {}
    for e in risk.read_json(index_path):
        lines, new = set(), None
        for line in risk.read_text(os.path.join(ctx, e["patch"])).splitlines():
            m = HUNK.match(line)
            if m:
                new = int(m.group(1))
            elif new is None or line.startswith(("-", "\\")):
                continue                     # file header, removed line, or "\ No newline at end of file"
            else:                            # added ("+") or context (" ") line on the new side
                lines.add(new)
                new += 1
        out[e["file"]] = lines
    return out


def post(a):
    res = risk.read_json(os.path.join(a.out, "result.json"))
    report = risk.read_text(os.path.join(a.out, "report.md"))
    rules = sorted({f["rule"] for f in res["findings"] if f["rule"] != "NEW"})
    marker = (f"<!-- clean-eval:pr-review tier={res['max_tier']} score={res['max_score']:.3f} "
              f"rules={','.join(rules)} rules_version={res.get('rules_version') or ''} -->")
    # Attach only comments on lines that are in the diff: GitHub rejects the whole review (422) otherwise.
    on_diff = commentable_lines(getattr(a, "ctx", None))
    comments, detached = [], []
    for f in res["findings"]:
        if not (f["file"] and f["line"]):
            continue
        if on_diff is None or f["line"] in on_diff.get(f["file"], ()):
            comments.append({"path": f["file"], "line": f["line"], "side": "RIGHT",
                             "body": f"[{f['rule']}][{f['severity']}] {f['summary']}"
                                     + (f"\n\n**Fix:** {f['fix']}" if f["fix"] else "")})
        else:
            detached.append(f"- [{f['rule']}][{f['severity']}] `{f['file']}:{f['line']}`: {f['summary']}")
    extra = ("\n\n**Not attached inline** (line is outside the diff):\n" + "\n".join(detached)) if detached else ""
    body = f"{report.strip()}{extra}\n\n{marker}"
    event = "REQUEST_CHANGES" if res["verdict"] == "request_changes" else "COMMENT"
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{a.pr}/reviews"
    posted = None
    # inline comments 422 if a line isn't in the diff; own PRs can't request changes
    for ev, cm in ((event, comments), (event, []), ("COMMENT", comments), ("COMMENT", [])):
        if posted is not None:
            break
        payload = {"commit_id": a.head, "body": body, "event": ev, **({"comments": cm} if cm else {})}
        ok, out, err = gh("api", "-X", "POST", endpoint, "--input", "-", stdin=json.dumps(payload))
        if ok:
            posted = json.loads(out)
            print(f"posted review on #{a.pr}: event={ev}, inline comments={len(cm)}")
        else:
            print(f"::warning::review post failed (event={ev}, comments={len(cm)}): {redact(err)[0][:300]}", file=sys.stderr)
    if posted is None:
        return 1

    # A newer review supersedes our earlier "changes requested" ones; dismiss them so a fixed PR isn't left blocked.
    me, my_id = (posted.get("user") or {}).get("login"), posted.get("id")
    ok, out, err = gh("api", "--paginate", endpoint)
    if not ok:
        print(f"::warning::could not list reviews to dismiss stale ones: {err[:200]}", file=sys.stderr)
        return 0
    for r in history._loads_multi(out) or []:
        if (r.get("id") != my_id and r.get("state") == "CHANGES_REQUESTED" and (r.get("user") or {}).get("login") == me
                and MARKER_RE.search(r.get("body") or "")):
            ok, _, err = gh("api", "-X", "PUT", f"{endpoint}/{r['id']}/dismissals",
                            "-f", f"message=Superseded by the clean-eval review of {a.head[:8]}", "-f", "event=DISMISS")
            print(f"dismissed stale review {r['id']}" if ok else f"::warning::could not dismiss review {r['id']}: {err[:200]}",
                  file=sys.stderr if not ok else sys.stdout)
    return 0


def redact_file(a):
    if os.path.exists(a.path):
        text, n = redact(risk.read_text(a.path, errors="replace"))
        if n:
            risk.write_text(a.path, text)
            print(f"::warning::clean-eval redacted {n} secret-looking value(s) from {os.path.basename(a.path)}",
                  file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize")
    f.add_argument("--skill", choices=["pr-review", "eval", "risk-heat-map"], required=True)
    f.add_argument("--ctx", required=True)
    f.add_argument("--agent", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--workspace", default=".")
    f.add_argument("--apply", action="store_true")
    f.add_argument("--gh-output", required=True)
    p = sub.add_parser("post")
    p.add_argument("--pr", required=True)
    p.add_argument("--ctx", help="context dir with diff/index.json, to attach only comments on diff lines")
    p.add_argument("--out", required=True)
    p.add_argument("--head", required=True)
    r = sub.add_parser("redact-file")
    r.add_argument("path")
    a = ap.parse_args(argv)
    sys.exit({"finalize": finalize, "post": post, "redact-file": redact_file}[a.cmd](a))


if __name__ == "__main__":
    main()
