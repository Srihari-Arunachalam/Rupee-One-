import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

app = FastAPI(title="PayShield Control Tower")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # demo only


# ---------- in-memory stores (swap for PostgreSQL / Redis in production) ----------
PAYMENTS, IDEM, AUDIT, LEDGER, EXCEPTIONS, RECENT = {}, {}, [], [], [], []
SANCTIONS = ["ahmed khan ali", "viktor petrov", "hassan al rashid"]
FINGERPRINT_WINDOW = 120  # seconds

TRANSITIONS = {
    "INITIATED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"SCREENED", "FAILED"},
    "SCREENED": {"ROUTED", "HELD", "BLOCKED"},
    "HELD": {"ROUTED", "BLOCKED"},
    "ROUTED": {"SETTLED", "FAILED"},
    "SETTLED": {"REVERSED"},
    "FAILED": set(),
    "BLOCKED": set(),
    "REVERSED": set(),
}

RAILS = {
    "UPI": {"cost": 0.0, "p95": 3, "min": 0, "max": 100_000, "rel": 0.97},
    "IMPS": {"cost": 2.5, "p95": 5, "min": 0, "max": 500_000, "rel": 0.98},
    "RTGS": {"cost": 25.0, "p95": 30, "min": 200_000, "max": 10**9, "rel": 0.99},
    "NEFT": {"cost": 1.0, "p95": 1800, "min": 0, "max": 10**9, "rel": 0.995},
}
RAIL_STATE = {}
USERS, TOKENS = {}, {}
SEED_USERS = {"ravi": 1_000_000, "priya": 100_000, "suresh": 100_000, "meena": 100_000}
DEFAULT_BALANCE = 100_000  # demo wallet for unknown payers
SECRET = "demo-secret"


def reset():
    for s in (PAYMENTS, IDEM, RAIL_STATE, USERS, TOKENS):
        s.clear()
    for s in (AUDIT, LEDGER, EXCEPTIONS, RECENT):
        s.clear()
    for name in RAILS:
        RAIL_STATE[name] = {"down": False, "fails": 0, "open_until": 0.0}
    for n, b in SEED_USERS.items():
        USERS[n] = {"name": n.title(), "balance": b}


reset()


def now():
    return datetime.now(timezone.utc).isoformat()


def user(name):
    return USERS.setdefault(name.strip().lower(), {"name": name.strip().title(), "balance": DEFAULT_BALANCE})


def bad_payee(msg):
    return HTTPException(422, {"code": "INVALID_PAYEE", "msg": msg})


def normalize_payee(raw):
    """UPI ID / phone / account:IFSC / plain name -> one canonical shape."""
    s = raw.strip()
    if "@" in s:
        if not re.fullmatch(r"[\w.\-]{2,}@[A-Za-z]{2,}", s):
            raise bad_payee("UPI ID should look like name@bank")
        hint = re.sub(r"[._\-\d]+", " ", s.split("@")[0]).strip()
        return {"type": "UPI", "value": s.lower(), "display": s.lower(), "name_hint": hint}
    digits = re.sub(r"[\s\-+]", "", s)
    if ":" in s:
        acct, ifsc = [x.strip() for x in s.split(":", 1)]
        if not re.fullmatch(r"\d{9,18}", acct) or not re.fullmatch(r"[A-Za-z]{4}0[A-Za-z0-9]{6}", ifsc):
            raise bad_payee("Bank account should be account:IFSC, e.g. 123456789012:HDFC0001234")
        return {"type": "ACCOUNT", "value": f"{acct}:{ifsc.upper()}", "display": f"••••{acct[-4:]} ({ifsc.upper()})", "name_hint": ""}
    if re.fullmatch(r"(91)?[6-9]\d{9}", digits):
        return {"type": "PHONE", "value": digits[-10:], "display": f"+91 ••••••{digits[-4:]}", "name_hint": ""}
    if re.fullmatch(r"[\d\s\-+]+", s):
        raise bad_payee("Phone numbers need 10 digits starting 6-9")
    return {"type": "NAME", "value": s, "display": s, "name_hint": s}


def rail_status(n):
    st = RAIL_STATE[n]
    if time.time() < st["open_until"]:
        return "circuit_open"
    return "down" if st["down"] else "up"


# ---------- audit trail (hash chain) ----------
def _hash(prev, entry):
    body = {k: entry[k] for k in ("seq", "ts", "event", "data")}
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()


def audit(event, data):
    prev = AUDIT[-1]["hash"] if AUDIT else "GENESIS"
    entry = {"seq": len(AUDIT), "ts": now(), "event": event, "data": data, "prev": prev}
    entry["hash"] = _hash(prev, entry)
    AUDIT.append(entry)


def verify_chain():
    prev = "GENESIS"
    for e in AUDIT:
        if e["prev"] != prev or e["hash"] != _hash(prev, e):
            return {"valid": False, "broken_at": e["seq"]}
        prev = e["hash"]
    return {"valid": True, "entries": len(AUDIT)}


# ---------- state machine ----------
def move(p, new, reason):
    if new not in TRANSITIONS[p["status"]]:
        raise ValueError(f"Illegal transition {p['status']} -> {new}")
    p["timeline"].append({"from": p["status"], "to": new, "reason": reason, "ts": now()})
    audit("STATE", {"payment": p["id"], "from": p["status"], "to": new, "reason": reason})
    p["status"] = new


# ---------- compliance ----------
def screen_sanctions(*names):
    best, hit = 0, None
    for n in names:
        for s in SANCTIONS:
            score = fuzz.token_sort_ratio(n.lower().strip(), s)
            if score > best:
                best, hit = score, s
    return round(best), hit


def fraud_score(p, hour):
    score, reasons = 0, []
    if p["amount"] >= 200_000:
        score += 50
        reasons.append("very high amount")
    elif p["amount"] >= 50_000:
        score += 30
        reasons.append("high amount")
    if hour < 5:
        score += 25
        reasons.append("odd hour")
    recent = [r for r in RECENT if r["payer"] == p["payer"] and time.time() - r["t"] < 60]
    if len(recent) >= 5:
        score += 40
        reasons.append("velocity: 5+ payments in 60s")
    return score, reasons


def compliance(p, hour):
    s_score, s_hit = screen_sanctions(*[n for n in (p["payer"], p["payee_name"]) if n])
    f_score, f_reasons = fraud_score(p, hour)
    if s_score >= 90:
        return {"decision": "BLOCK", "reason": f"Sanctions match '{s_hit}' ({s_score}%)"}
    if f_score >= 90:
        return {"decision": "BLOCK", "reason": f"Fraud score {f_score}: {', '.join(f_reasons)}"}
    if s_score >= 75:
        return {"decision": "REVIEW", "reason": f"Possible sanctions match '{s_hit}' ({s_score}%)"}
    if f_score >= 50:
        return {"decision": "REVIEW", "reason": f"Fraud score {f_score}: {', '.join(f_reasons)}"}
    return {"decision": "ALLOW", "reason": f"Clear (sanctions {s_score}%, fraud {f_score})"}


# ---------- smart router + circuit breaker ----------
def rank_rails(amount, deadline):
    eligible = [n for n, r in RAILS.items() if r["min"] <= amount <= r["max"]]
    meets = sorted([n for n in eligible if RAILS[n]["p95"] <= deadline], key=lambda n: RAILS[n]["cost"])
    rest = sorted([n for n in eligible if n not in meets], key=lambda n: RAILS[n]["p95"])
    return meets + rest


def try_rail(name):
    st = RAIL_STATE[name]
    if time.time() < st["open_until"]:
        return False, "circuit open"
    if st["down"]:
        st["fails"] += 1
        if st["fails"] >= 3:
            st["open_until"] = time.time() + 30
        return False, "rail down"
    st["fails"] = 0
    return True, "ok"


def proceed(p):
    move(p, "ROUTED", "routing")
    if user(p["payer"])["balance"] < p["amount"]:
        move(p, "FAILED", "insufficient funds")
        return
    for rail in rank_rails(p["amount"], p["deadline_seconds"]):
        ok, why = try_rail(rail)
        p["attempts"].append({"rail": rail, "result": why})
        if ok:
            p["rail"], p["eta_seconds"] = rail, RAILS[rail]["p95"]
            move(p, "SETTLED", f"settled via {rail}")
            LEDGER.append({"payment": p["id"], "debit": p["payer"], "credit": p["payee"], "amount": p["amount"]})
            user(p["payer"])["balance"] -= p["amount"]
            if p["payee"].lower() in USERS:
                USERS[p["payee"].lower()]["balance"] += p["amount"]
            return
    move(p, "FAILED", "all rails failed")
    EXCEPTIONS.append({"payment": p["id"], "type": "ROUTING_FAILED"})


# ---------- API ----------
class PaymentIn(BaseModel):
    payer: str
    payee: str
    payee_name: str | None = None  # optional name for screening when payee is a UPI ID / phone / account
    amount: float = Field(gt=0)
    currency: str = "INR"
    deadline_seconds: int = 60
    confirm_duplicate: bool = False
    hour: int | None = None  # demo override for odd-hour fraud rule


@app.post("/payments")
def create_payment(body: PaymentIn, idempotency_key: str = Header(...)):
    canon = body.model_dump(exclude={"confirm_duplicate"})
    body_hash = hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()

    if idempotency_key in IDEM:
        saved = IDEM[idempotency_key]
        if saved["hash"] != body_hash:
            raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_REUSED", "msg": "Same key, different body"})
        return JSONResponse(saved["response"], saved["status"], headers={"Idempotent-Replay": "true"})

    fp = f"{body.payer}|{body.payee}|{body.amount}".lower()
    dup = [r for r in RECENT if r["fp"] == fp and time.time() - r["t"] < FINGERPRINT_WINDOW]
    if dup and not body.confirm_duplicate:
        raise HTTPException(409, {"code": "POSSIBLE_DUPLICATE",
                                  "msg": "Same payer, payee and amount seen recently. Resend with confirm_duplicate=true if intended."})

    info = normalize_payee(body.payee)
    if user(body.payer)["balance"] < body.amount:
        raise HTTPException(422, {"code": "INSUFFICIENT_FUNDS", "msg": "Wallet balance is lower than the amount"})

    p = {"id": "PAY-" + uuid.uuid4().hex[:8].upper(), "payer": body.payer, "payee": info["value"],
         "payee_type": info["type"], "payee_display": info["display"], "payee_name": body.payee_name or info["name_hint"],
         "amount": body.amount, "currency": body.currency, "deadline_seconds": body.deadline_seconds,
         "status": "INITIATED", "timeline": [], "attempts": [], "rail": None, "eta_seconds": None,
         "compliance": None}
    PAYMENTS[p["id"]] = p
    p["share_token"] = hashlib.sha256((p["id"] + SECRET).encode()).hexdigest()[:12]
    TOKENS[p["share_token"]] = p["id"]
    audit("CREATED", {"payment": p["id"], "key": idempotency_key})

    move(p, "VALIDATED", "schema ok")
    hour = body.hour if body.hour is not None else datetime.now().hour
    p["compliance"] = compliance(p, hour)
    move(p, "SCREENED", p["compliance"]["reason"])
    RECENT.append({"fp": fp, "payer": body.payer, "t": time.time()})

    d = p["compliance"]["decision"]
    if d == "BLOCK":
        move(p, "BLOCKED", p["compliance"]["reason"])
    elif d == "REVIEW":
        move(p, "HELD", p["compliance"]["reason"])
        EXCEPTIONS.append({"payment": p["id"], "type": "COMPLIANCE_REVIEW", "reason": p["compliance"]["reason"]})
    else:
        proceed(p)

    response = json.loads(json.dumps(p))
    IDEM[idempotency_key] = {"hash": body_hash, "response": response, "status": 201}
    return JSONResponse(response, 201)


@app.get("/payments/{pid}")
def get_payment(pid: str):
    if pid not in PAYMENTS:
        raise HTTPException(404, "not found")
    return PAYMENTS[pid]


@app.get("/exceptions")
def list_exceptions():
    return EXCEPTIONS


@app.post("/exceptions/{pid}/resolve")
def resolve(pid: str, approve: bool):
    p = PAYMENTS.get(pid)
    if not p or p["status"] != "HELD":
        raise HTTPException(409, "payment is not on hold")
    if approve:
        proceed(p)
    else:
        move(p, "BLOCKED", "officer rejected")
    EXCEPTIONS[:] = [e for e in EXCEPTIONS if e["payment"] != pid]
    return p


@app.post("/chaos/rails/{name}")
def chaos(name: str, down: bool):
    if name not in RAILS:
        raise HTTPException(404, "unknown rail")
    RAIL_STATE[name].update(down=down, fails=0, open_until=0.0)
    audit("CHAOS", {"rail": name, "down": down})
    return {"rail": name, "down": down}


class BankRow(BaseModel):
    reference: str
    amount: float


def run_reconcile(rows):
    issues, seen = [], set()
    for r in rows:
        if r["reference"] in seen:
            issues.append({"ref": r["reference"], "type": "DUPLICATE", "hint": "Bank listed this twice"})
            continue
        seen.add(r["reference"])
        p = PAYMENTS.get(r["reference"])
        if not p:
            issues.append({"ref": r["reference"], "type": "UNKNOWN_AT_BANK", "hint": "Not in our ledger"})
        elif abs(p["amount"] - r["amount"]) > 0.01:
            diff = round(p["amount"] - r["amount"], 2)
            hint = "Likely fee deduction" if 0 < diff < p["amount"] * 0.01 else "Amount differs, investigate"
            issues.append({"ref": r["reference"], "type": "AMOUNT_MISMATCH", "diff": diff, "hint": hint})
    for pid, p in PAYMENTS.items():
        if p["status"] == "SETTLED" and pid not in seen:
            issues.append({"ref": pid, "type": "MISSING_AT_BANK", "hint": "Check timing, may post next day"})
    for i in issues:
        EXCEPTIONS.append({"payment": i["ref"], "type": "RECON_" + i["type"], "reason": i["hint"]})
    return {"checked": len(rows), "matched": len(seen) - len([i for i in issues if i["type"] in ("AMOUNT_MISMATCH", "UNKNOWN_AT_BANK")]), "issues": issues}


@app.post("/reconcile")
def reconcile(rows: list[BankRow]):
    return run_reconcile([r.model_dump() for r in rows])


@app.get("/audit/verify")
def audit_verify():
    return verify_chain()


# ================= consumer-app endpoints =================
class QuoteIn(BaseModel):
    amount: float = Field(gt=0)
    deadline_seconds: int = 60


@app.post("/quote")
def quote(q: QuoteIn):
    """Settlement Promise: rails ranked for this amount + deadline, with ETA, fee and confidence."""
    options = []
    for n in rank_rails(q.amount, q.deadline_seconds):
        r, st = RAILS[n], rail_status(n)
        meets = r["p95"] <= q.deadline_seconds
        options.append({
            "rail": n, "status": st, "fee": r["cost"], "eta_seconds": r["p95"],
            "expected_by": (datetime.now(timezone.utc) + timedelta(seconds=r["p95"])).isoformat(),
            "meets_deadline": meets,
            "confidence": r["rel"] if (st == "up" and meets) else 0.0,
        })
    healthy = [o for o in options if o["status"] == "up"]
    if not healthy:
        raise HTTPException(503, {"code": "NO_RAIL_AVAILABLE", "msg": "All payment rails are down right now"})
    best = healthy[0]
    return {"recommended": best["rail"], "meets_deadline": best["meets_deadline"], "options": options,
            "summary": f"{best['rail']}: {round(best['confidence'] * 100)}% chance within {best['eta_seconds']}s, fee ₹{best['fee']}"
            if best["meets_deadline"] else f"No rail can meet the deadline. Fastest: {best['rail']} in about {best['eta_seconds']}s"}


@app.get("/rails")
def rails():
    out = [{"rail": n, "status": rail_status(n), "p95_seconds": r["p95"], "fee": r["cost"]} for n, r in RAILS.items()]
    up = [o["rail"] for o in sorted(out, key=lambda o: o["p95_seconds"]) if o["status"] == "up"]
    banners = [f"{o['rail']} is unavailable. Payments are rerouted to {up[0]}." for o in out if o["status"] != "up" and up]
    return {"rails": out, "banner": " ".join(banners) or None}


@app.get("/users/{name}")
def get_user(name: str):
    if name.lower() not in USERS:
        raise HTTPException(404, "unknown user")
    return USERS[name.lower()]


@app.get("/users/{name}/payments")
def user_payments(name: str):
    return [{k: p[k] for k in ("id", "payee_display", "amount", "status", "rail", "share_token")}
            for p in reversed(list(PAYMENTS.values())) if p["payer"].lower() == name.lower()]


STEPS = [("Received", {"VALIDATED"}), ("Checked", {"SCREENED"}), ("Sent to bank", {"ROUTED"}), ("Delivered", {"SETTLED"})]


def passport_view(p, public=False):
    reached = {t["to"] for t in p["timeline"]}
    steps = [{"step": name, "done": bool(keys & reached)} for name, keys in STEPS]
    st, c = p["status"], p["compliance"] or {}
    headline, nxt = {
        "SETTLED": (f"Delivered via {p['rail']}", "Nothing more to do."),
        "HELD": ("Under review", "A compliance officer will review this payment. You will be notified of the outcome."),
        "BLOCKED": ("Payment stopped", "This payment was not sent and no money was taken."),
        "FAILED": ("Payment failed", "No money was taken. You can try again."),
    }.get(st, ("In progress", "This should update within seconds."))
    out = {"id": p["id"], "status": st, "headline": headline, "next_step": nxt, "steps": steps,
           "amount": p["amount"], "payee": p["payee_display"], "rail": p["rail"], "eta_seconds": p["eta_seconds"],
           "reason": c.get("reason") if st in ("HELD", "BLOCKED") else None}
    if not public:
        out.update(timeline=p["timeline"], rail_attempts=p["attempts"], compliance=p["compliance"],
                   share_url=f"/track/{p['share_token']}")
    return out


@app.get("/payments/{pid}/passport")
def passport(pid: str):
    if pid not in PAYMENTS:
        raise HTTPException(404, "not found")
    return passport_view(PAYMENTS[pid])


@app.get("/track/{token}")
def track(token: str):
    """Public tracking link for the receiver: no payer details, no internal scores."""
    if token not in TOKENS:
        raise HTTPException(404, "not found")
    v = passport_view(PAYMENTS[TOKENS[token]], public=True)
    v["reason"] = None
    return v


# ---------- statement import with format auto-detection ----------
DATE_RE = re.compile(r"^(\d{2}[-/]\d{2}[-/]\d{4}|\d{4}-\d{2}-\d{2}|\d{8})$")
REF_RE = re.compile(r"^[A-Za-z]{2,}[-A-Za-z0-9]*\d[-A-Za-z0-9]*$")
AMT_RE = re.compile(r"^-?[\d,]+(\.\d{1,2})?$")


def fixed_columns(lines):
    w = max(len(l) for l in lines)
    lines = [l.ljust(w) for l in lines]
    cols, start = [], None
    for i in range(w + 1):
        blank = i == w or all(l[i] == " " for l in lines)
        if not blank and start is None:
            start = i
        elif blank and start is not None:
            cols.append((start, i))
            start = None
    return cols


def detect_statement(text):
    lines = [l.rstrip("\n") for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        raise HTTPException(422, {"code": "STATEMENT_TOO_SHORT", "msg": "Need at least 2 lines to detect the format"})
    delim = next((d for d in (",", ";", "|", "\t") if len({l.count(d) for l in lines}) == 1 and lines[0].count(d) >= 2), None)
    if delim:
        fmt, grid = "csv", [[c.strip() for c in l.split(delim)] for l in lines]
    else:
        cols = fixed_columns(lines)
        fmt, grid = "fixed-width", [[l[a:b].strip() for a, b in cols] for l in lines]
    first = grid[0]
    header = not any(AMT_RE.match(c) or REF_RE.match(c) or DATE_RE.match(c) for c in first)
    data = grid[1:] if header else grid

    def score(rx, i):
        vals = [r[i] for r in data if i < len(r)]
        return sum(bool(rx.match(v)) for v in vals) / max(len(vals), 1)

    n = max(len(r) for r in data)
    best = {}
    for kind, rx in (("date", DATE_RE), ("reference", REF_RE), ("amount", AMT_RE)):
        cand = [(score(rx, i), i) for i in range(n) if i not in [b["index"] for b in best.values()]]
        if kind == "amount":  # prefer columns that carry decimals
            cand = [(sc + (0.01 if any("." in r[i] for r in data if i < len(r)) else 0), i) for sc, i in cand]
        sc, i = max(cand)
        if sc >= 0.6:
            best[kind] = {"index": i, "confidence": round(min(sc, 1.0), 2), "header": first[i] if header else None}
    if "reference" not in best or "amount" not in best:
        raise HTTPException(422, {"code": "FORMAT_NOT_RECOGNISED", "msg": "Could not find reference and amount columns", "detected": best})
    rows = []
    for r in data:
        try:
            rows.append({"reference": r[best["reference"]["index"]],
                         "amount": float(r[best["amount"]["index"]].replace(",", "")),
                         "date": r[best["date"]["index"]] if "date" in best else None})
        except (ValueError, IndexError):
            continue
    return {"format": fmt, "delimiter": delim, "has_header": header, "columns": best, "rows": rows, "skipped_rows": len(data) - len(rows)}


class StatementIn(BaseModel):
    text: str


@app.post("/statements/check")
def check_statement(body: StatementIn):
    det = detect_statement(body.text)
    result = run_reconcile(det["rows"])
    return {"detected": {k: det[k] for k in ("format", "delimiter", "has_header", "columns", "skipped_rows")}, **result}
