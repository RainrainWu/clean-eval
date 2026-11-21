#!/usr/bin/env python3
"""Deterministic half of the clean-eval action: validate the agent's result.json, derive the
fields that gate CI from code (not from model text), emit step outputs, and post PR reviews.

The agent only *proposes* findings. Tier/score come from risk.py, verdict/blockers are derived
from the validated findings, and posting happens here — so the agent never needs a write token,
and nothing it writes reaches $GITHUB_OUTPUT unvalidated.

  result.py validate --skill pr-review --result R --risk RISK.json --gh-output F [--rules-version V]
  result.py validate --skill eval      --result R --gh-output F
  result.py post --pr N --result R --report report.md --head SHA
"""
import argparse
import json
import re
import subprocess
import sys

RULE_RE = re.compile(r"^([A-Z]{1,4}-\d{2,3}|NEW)$")
SEVERITIES = ("blocker", "major", "minor")
TIERS = ("Low", "Medium", "High", "Critical")
MARKER_RE = re.compile(r"<!--\s*clean-eval:[^>]*-->")


class Invalid(Exception):
    pass


def _clean_str(v, field, maxlen=4000):
    if not isinstance(v, str):
        raise Invalid(f"{field} must be a string")
    return v[:maxlen]


def _one_line(v, field):
    v = _clean_str(v, field, 300)
    if "\n" in v or "\r" in v:
        raise Invalid(f"{field} must be a single line")
    return v


def validate_pr_review(r, risk):
    findings = []
    for i, f in enumerate(r.get("findings") or []):
        if not isinstance(f, dict):
            raise Invalid(f"findings[{i}] must be an object")
        rule = _one_line(f.get("rule", ""), f"findings[{i}].rule")
        if not RULE_RE.match(rule):
            raise Invalid(f"findings[{i}].rule {rule!r} is not a rule ID or NEW")
        sev = f.get("severity")
        if sev not in SEVERITIES:
            raise Invalid(f"findings[{i}].severity must be one of {SEVERITIES}")
        line = f.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise Invalid(f"findings[{i}].line must be a positive integer or null")
        findings.append({
            "rule": rule, "severity": sev,
            "file": _one_line(f.get("file") or "", f"findings[{i}].file") or None,
            "line": line,
            "summary": _clean_str(f.get("summary", ""), f"findings[{i}].summary", 1000),
            "fix": _clean_str(f.get("fix", ""), f"findings[{i}].fix", 2000) if f.get("fix") else "",
        })
    blockers = sum(f["severity"] == "blocker" for f in findings)
    majors = sum(f["severity"] == "major" for f in findings)
    tier = risk.get("max_tier")
    if tier not in TIERS:
        raise Invalid("risk.json has no valid max_tier")
    return {
        "pr": risk.get("pr"),
        # gate-relevant fields are derived, never taken from model text
        "verdict": "request_changes" if blockers else ("comment" if majors else "approve"),
        "max_tier": tier,
        "max_score": float(risk.get("max_score", 0.0)),
        "blockers": blockers,
        "findings": findings,
    }


def validate_eval(r):
    out = {}
    for k in ("prs", "defective_prs"):
        v = r.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            raise Invalid(f"{k} must be an integer")
        out[k] = v
    auc = r.get("auc")
    out["auc"] = None if auc is None else round(float(auc), 3)
    if not isinstance(r.get("applied", False), bool):
        raise Invalid("applied must be a boolean")
    out["applied"] = r.get("applied", False)
    recs = r.get("recommendations") or []
    if not isinstance(recs, list):
        raise Invalid("recommendations must be a list")
    out["recommendations"] = [{k: _clean_str(str(x.get(k, "")), f"recommendations.{k}", 2000)
                               for k in ("target", "change", "evidence")} | {"applied": bool(x.get("applied"))}
                              for x in recs if isinstance(x, dict)]
    return out


def cmd_validate(a):
    try:
        raw = json.load(open(a.result))
        if not isinstance(raw, dict):
            raise Invalid("result.json must be an object")
        if a.skill == "pr-review":
            risk = json.load(open(a.risk))
            res = validate_pr_review(raw, risk)
            res["rules_version"] = a.rules_version
            outputs = {"verdict": res["verdict"], "max-tier": res["max_tier"],
                       "max-score": f"{res['max_score']:.3f}", "blockers": str(res["blockers"])}
        else:
            res = validate_eval(raw)
            outputs = {"auc": "" if res["auc"] is None else f"{res['auc']:.3f}",
                       "applied": "true" if res["applied"] else "false"}
    except (Invalid, ValueError, TypeError, KeyError, OSError) as e:
        print(f"::error::clean-eval: invalid {a.result}: {e}", file=sys.stderr)
        return 2
    json.dump(res, open(a.result, "w"), indent=2)  # replace with the validated/derived version
    with open(a.gh_output, "a") as o:
        for k, v in outputs.items():
            o.write(f"{k}={v}\n")  # values are enums/numbers built above: no newlines possible
    print(json.dumps(outputs))
    return 0


def gh_review(pr, payload):
    r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews", "--input", "-"],
                       input=json.dumps(payload), capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def cmd_post(a):
    res = json.load(open(a.result))
    report = open(a.report).read() if a.report else ""
    report = MARKER_RE.sub("", report).strip()  # the agent can't forge or duplicate the marker
    rules = sorted({f["rule"] for f in res["findings"] if f["rule"] != "NEW"})
    marker = (f"<!-- clean-eval:pr-review tier={res['max_tier']} score={res['max_score']:.3f} "
              f"rules={','.join(rules)} rules_version={res.get('rules_version') or ''} -->")
    body = f"{report}\n\n{marker}"
    comments = [{"path": f["file"], "line": f["line"], "side": "RIGHT",
                 "body": f"[{f['rule']}][{f['severity']}] {f['summary']}" + (f"\n\n**Fix:** {f['fix']}" if f["fix"] else "")}
                for f in res["findings"] if f["file"] and f["line"]]
    event = "REQUEST_CHANGES" if res["verdict"] == "request_changes" else "COMMENT"
    attempts = [(event, comments), (event, []), ("COMMENT", comments), ("COMMENT", [])]
    for ev, cm in attempts:  # inline comments 422 if a line isn't in the diff; own PRs can't request changes
        payload = {"commit_id": a.head, "body": body, "event": ev}
        if cm:
            payload["comments"] = cm
        ok, msg = gh_review(a.pr, payload)
        if ok:
            print(f"posted review on #{a.pr}: event={ev}, inline comments={len(cm)}")
            return 0
        print(f"::warning::review post failed (event={ev}, comments={len(cm)}): {msg[:300]}", file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--skill", choices=["pr-review", "eval"], required=True)
    v.add_argument("--result", required=True)
    v.add_argument("--risk")
    v.add_argument("--rules-version", default="")
    v.add_argument("--gh-output", required=True)
    p = sub.add_parser("post")
    p.add_argument("--pr", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--report")
    p.add_argument("--head", required=True)
    a = ap.parse_args()
    sys.exit(cmd_validate(a) if a.cmd == "validate" else cmd_post(a))


if __name__ == "__main__":
    main()
