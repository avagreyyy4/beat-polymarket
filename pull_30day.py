#!/usr/bin/env python3
# pull30.py
# Fetch Polymarket binary markets (Yes/No) ending ~N days from now and their best quotes.

import argparse
import datetime as dt
import json
from typing import Any, Dict, List, Optional, Tuple

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

YES_ALIASES = {"yes", "y", "true", "1"}
NO_ALIASES = {"no", "n", "false", "0"}

# ------------------------- time / window helpers ------------------------- #

def _iso_utc(d: dt.date, end_of_day: bool = False) -> str:
    return f"{d:%Y-%m-%d}T{'23:59:59' if end_of_day else '00:00:00'}Z"

def window_days_ahead(days_ahead: int = 30, pad_days: int = 7) -> Tuple[str, str]:
    today = dt.date.today()
    target = today + dt.timedelta(days=days_ahead)
    start = target - dt.timedelta(days=pad_days)
    end = target + dt.timedelta(days=pad_days)
    return _iso_utc(start, False), _iso_utc(end, True)

# ------------------------- HTTP / API helpers ------------------------- #

def _fetch_markets(params: Dict[str, str], verbose: bool = False) -> List[Dict[str, Any]]:
    url = f"{GAMMA}/markets"
    if verbose:
        print(f"[Gamma] GET {url} params={params}")
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_markets_due_in(days_ahead=30, pad_days=7, limit=200,
                       require_accepting=True, verbose=False) -> List[Dict[str, Any]]:
    end_min, end_max = window_days_ahead(days_ahead, pad_days)
    params = {
        "closed": "false",
        "end_date_min": end_min,
        "end_date_max": end_max,
        "limit": str(limit),
        "order": "endDate",
        "ascending": "true",
    }
    if require_accepting:
        params["acceptingOrders"] = "true"

    mkts = _fetch_markets(params, verbose=verbose)

    if not mkts:
        if verbose:
            print("[Gamma] Empty; retry wider band and client-side filter.")
        end_min2, end_max2 = window_days_ahead(days_ahead, pad_days * 2)
        retry = {
            "end_date_min": end_min2,
            "end_date_max": end_max2,
            "limit": str(limit),
            "order": "endDate",
            "ascending": "true",
        }
        mkts = _fetch_markets(retry, verbose=verbose)
        mkts = [m for m in mkts if not m.get("closed") and (m.get("acceptingOrders") is True)]
    return mkts

# ------------------------- outcome & token parsing (binary only) ------------------------- #

def _coerce_outcomes(raw) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        outs = [str(x).strip() for x in raw if str(x).strip() != ""]
    elif isinstance(raw, str):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                tmp = json.loads(s); outs = [str(x).strip() for x in tmp if str(x).strip() != ""]
            except Exception:
                outs = [t.strip() for t in s.split(",") if t.strip() != ""]
        else:
            outs = [t.strip() for t in s.split(",") if t.strip() != ""]
    else:
        return None
    if len(outs) != 2:
        return None
    return [o.capitalize() for o in outs]

def _parse_outcome_prices(raw) -> Optional[List[float]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        try: return [float(x) for x in raw]
        except Exception: return None
    s = str(raw).strip()
    if not s: return None
    try:
        if s.startswith("[") and s.endswith("]"):
            j = json.loads(s); return [float(x) for x in j]
        return [float(t) for t in s.split(",")]
    except Exception:
        return None

def is_binary_yes_no(m: Dict[str, Any]) -> bool:
    outs = _coerce_outcomes(m.get("outcomes"))
    if not outs: return False
    a, b = outs
    def _is_yes(x): return x.lower() in YES_ALIASES or x.lower() == "yes"
    def _is_no(x):  return x.lower() in NO_ALIASES  or x.lower() == "no"
    return (_is_yes(a) and _is_no(b)) or (_is_yes(b) and _is_no(a))

def parse_token_ids_field(m: Dict[str, Any]):
    return m.get("clobTokenIds") or m.get("clob_token_ids")

def parse_token_ids(raw) -> List[str]:
    if raw is None: return []
    if isinstance(raw, list): return [str(x) for x in raw]
    return [t.strip() for t in str(raw).split(",") if t.strip()]

# ------------------------- prices (CLOB) ------------------------- #

def fetch_prices_bulk(token_ids: List[str], verbose: bool = False) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Bulk /prices + /midpoints
    Returns token_id -> {"BUY": float|None, "SELL": float|None, "MID": float|None}
    """
    out: Dict[str, Dict[str, Optional[float]]] = {tid: {"BUY": None, "SELL": None, "MID": None} for tid in token_ids}
    if not token_ids: return out

    url_prices = f"{CLOB}/prices"
    url_mids   = f"{CLOB}/midpoints"

    # /prices variant A (wrapped)
    payloadA = {"params": [{"token_id": tid, "side": s} for tid in token_ids for s in ("BUY", "SELL")]}
    if verbose: print(f"[CLOB] POST {url_prices} payloadA entries={len(payloadA['params'])}")
    r = requests.post(url_prices, json=payloadA, timeout=30)
    if not r.ok:
        # /prices variant B (raw list)
        payloadB = [{"token_id": tid, "side": s} for tid in token_ids for s in ("BUY", "SELL")]
        if verbose: print(f"[CLOB] POST {url_prices} payloadB entries={len(payloadB)} statusA={r.status_code}")
        r = requests.post(url_prices, json=payloadB, timeout=30)
    r.raise_for_status()
    resp = r.json()

    def _to_float(x):
        try: return float(x)
        except Exception: return None

    if isinstance(resp, dict):
        for tid, sides in (resp or {}).items():
            if tid not in out: out[tid] = {"BUY": None, "SELL": None, "MID": None}
            for k, v in (sides or {}).items():
                key = str(k).upper()
                if key in ("BUY", "SELL"):
                    out[tid][key] = _to_float(v)
    elif isinstance(resp, list):
        for row in resp:
            tid = str(row.get("token_id"))
            side = str(row.get("side", "")).upper()
            price = _to_float(row.get("price"))
            if tid in out and side in ("BUY", "SELL"):
                out[tid][side] = price

    # /midpoints (bulk)
    mid_payloadA = {"params": token_ids}
    if verbose: print(f"[CLOB] POST {url_mids} mids payloadA n={len(token_ids)}")
    mr = requests.post(url_mids, json=mid_payloadA, timeout=30)
    if not mr.ok:
        if verbose: print(f"[CLOB] POST {url_mids} mids payloadB after {mr.status_code}")
        mr = requests.post(url_mids, json=token_ids, timeout=30)
    if mr.ok:
        mids = mr.json()
        if isinstance(mids, dict):
            for tid, val in mids.items():
                try:
                    midv = float(val.get("mid")) if isinstance(val, dict) else float(val)
                    if tid in out:
                        out[tid]["MID"] = midv
                except Exception:
                    pass

    return out

def fetch_prices_single(token_id: str, verbose: bool = False) -> Dict[str, Optional[float]]:
    """
    Final fallback: GET /price BUY, GET /price SELL, GET /midpoint for a single token.
    """
    res = {"BUY": None, "SELL": None, "MID": None}
    try:
        r_buy = requests.get(f"{CLOB}/price", params={"token_id": token_id, "side": "BUY"}, timeout=15)
        if r_buy.ok: res["BUY"] = float(r_buy.json().get("price"))
    except Exception:
        pass
    try:
        r_sell = requests.get(f"{CLOB}/price", params={"token_id": token_id, "side": "SELL"}, timeout=15)
        if r_sell.ok: res["SELL"] = float(r_sell.json().get("price"))
    except Exception:
        pass
    try:
        r_mid = requests.get(f"{CLOB}/midpoint", params={"token_id": token_id}, timeout=15)
        if r_mid.ok:
            val = r_mid.json().get("mid")
            res["MID"] = float(val) if val is not None else None
    except Exception:
        pass
    if verbose and all(v is None for v in res.values()):
        print(f"[CLOB] no quotes for token {token_id}")
    return res

# ------------------------- summarization ------------------------- #

def _safe_float(x) -> Optional[float]:
    try: return float(x)
    except Exception: return None

def summarize_binary_market(m: Dict[str, Any],
                            price_map: Dict[str, Dict[str, Optional[float]]],
                            verbose: bool = False) -> Dict[str, Any]:
    outs = _coerce_outcomes(m.get("outcomes")) or ["Yes", "No"]
    tids = parse_token_ids(parse_token_ids_field(m))
    tids = tids[:2] if len(tids) >= 2 else tids

    # Ensure Yes is index 0 if possible
    if len(outs) == 2 and len(tids) == 2 and outs[0].lower() == "no" and outs[1].lower() == "yes":
        outs = [outs[1], outs[0]]
        tids = [tids[1], tids[0]]

    yes_tid = tids[0] if len(tids) > 0 else None
    no_tid  = tids[1] if len(tids) > 1 else None

    p_yes = price_map.get(yes_tid, {}) if yes_tid else {}
    p_no  = price_map.get(no_tid,  {}) if no_tid  else {}

    y_buy, y_sell, y_mid = p_yes.get("BUY"), p_yes.get("SELL"), p_yes.get("MID")
    n_buy, n_sell, n_mid = p_no.get("BUY"),  p_no.get("SELL"),  p_no.get("MID")

    # If no bulk quotes, try single-token fallbacks for missing tokens
    if yes_tid and (y_buy is None and y_sell is None and y_mid is None):
        p_yes = fetch_prices_single(yes_tid, verbose=verbose)
        y_buy, y_sell, y_mid = p_yes["BUY"], p_yes["SELL"], p_yes["MID"]
    if no_tid and (n_buy is None and n_sell is None and n_mid is None):
        p_no = fetch_prices_single(no_tid, verbose=verbose)
        n_buy, n_sell, n_mid = p_no["BUY"], p_no["SELL"], p_no["MID"]

    # Prefer MID; else (BUY+SELL)/2; else BUY
    q_yes_mid = None
    if y_mid is not None:
        q_yes_mid = y_mid
    elif (y_buy is not None) and (y_sell is not None):
        q_yes_mid = 0.5 * (y_buy + y_sell)
    elif y_buy is not None:
        q_yes_mid = y_buy

    # If we only have NO mid, infer YES mid = 1 - NO mid
    if q_yes_mid is None and n_mid is not None:
        q_yes_mid = 1.0 - n_mid

    # Gamma fallback: outcomePrices and/or bestBid/bestAsk (market-level)
    if q_yes_mid is None:
        op = _parse_outcome_prices(m.get("outcomePrices"))
        if op and len(op) >= 2:
            if outs and outs[0].lower() == "yes":
                yes_idx = 0
            elif outs and len(outs) > 1 and outs[1].lower() == "yes":
                yes_idx = 1
            else:
                yes_idx = 0
            if yes_idx < len(op):
                q_yes_mid = op[yes_idx]
        else:
            # try bestBid/bestAsk (Gamma sometimes includes these per side)
            try:
                bb = m.get("bestBid"); ba = m.get("bestAsk")
                # If Gamma reports YES side bestBid/bestAsk as scalars, take mid
                bb_f, ba_f = _safe_float(bb), _safe_float(ba)
                if bb_f is not None and ba_f is not None:
                    q_yes_mid = 0.5 * (bb_f + ba_f)
            except Exception:
                pass

    if q_yes_mid is None and verbose:
        print(f"[warn] q_yes_mid still None for slug={m.get('slug')} tokens={tids}")

    majority_side = "YES" if (q_yes_mid is not None and q_yes_mid >= 0.5) else ("NO" if q_yes_mid is not None else None)

    return {
        "id": m.get("id"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "category": m.get("category"),
        "endDate": m.get("endDate"),
        "binary": True,
        "yes": {"token_id": yes_tid, "best_buy": y_buy, "best_sell": y_sell},
        "no":  {"token_id": no_tid,  "best_buy": n_buy, "best_sell": n_sell},
        "q_yes_mid": q_yes_mid,
        "majority_side": majority_side
    }

# ------------------------- pipeline ------------------------- #

def pull_binary_markets_ending_in(days_ahead: int = 30,
                                  pad_days: int = 7,
                                  require_accepting: bool = True,
                                  verbose: bool = False) -> List[Dict[str, Any]]:
    mkts = get_markets_due_in(days_ahead=days_ahead,
                              pad_days=pad_days,
                              limit=200,
                              require_accepting=require_accepting,
                              verbose=verbose)
    mkts = [m for m in mkts if is_binary_yes_no(m)]

    token_ids: List[str] = []
    for m in mkts:
        token_ids.extend(parse_token_ids(parse_token_ids_field(m)))
    token_ids = list(dict.fromkeys(token_ids))

    bulk_prices = fetch_prices_bulk(token_ids, verbose=verbose) if token_ids else {}

    out = []
    for m in mkts:
        out.append(summarize_binary_market(m, bulk_prices, verbose=verbose))
    return out

# ------------------------- CLI ------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Pull Polymarket binary markets ending ~N days from now.")
    ap.add_argument("--days", type=int, default=30, help="Center window this many days ahead (default 30).")
    ap.add_argument("--pad", type=int, default=7, help="+/- pad days around the center (default 7).")
    ap.add_argument("--no-accepting", action="store_true",
                    help="Do NOT require acceptingOrders=true (include listed but not taking orders).")
    ap.add_argument("--verbose", action="store_true", help="Print called URLs and payload mode.")
    ap.add_argument("--out", type=str, default="", help="Write JSON to this path instead of stdout.")
    args = ap.parse_args()

    data = pull_binary_markets_ending_in(
        days_ahead=args.days,
        pad_days=args.pad,
        require_accepting=not args.no_accepting,
        verbose=args.verbose
    )

    out_obj = {
        "count": len(data),
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
        "days_ahead": args.days,
        "pad_days": args.pad,
        "require_accepting": not args.no_accepting,
        "markets": data
    }

    s = json.dumps(out_obj, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"Wrote {len(data)} markets to {args.out}")
    else:
        print(s)

if __name__ == "__main__":
    main()
