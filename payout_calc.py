#!/usr/bin/env python3
# majority90_cli.py
# Minimal interactive evaluator: "majority is right 90%" rule.
# You input: question, yes%, (optional no%), and your stake ($ you plan to spend).
# The script recommends a side, then computes EV and payouts, and prints a final total.

from dataclasses import dataclass
from typing import Optional, List, Dict

@dataclass
class Config:
    majority_accuracy: float = 0.90  # P(majority side is actually correct)
    min_ev: float = 0.0              # optional EV threshold per $1 to place a bet
    fee_rate: float = 0.0            # optional proportional fee on *winnings* (0.0 = none)

def _as_prob(x: float) -> float:
    """Accept 0–1 or 0–100 and normalize to 0–1."""
    return x / 100.0 if x > 1 else x

def _parse_float(prompt: str, allow_blank: bool = False) -> Optional[float]:
    while True:
        s = input(prompt).strip()
        if allow_blank and s == "":
            return None
        try:
            return float(s)
        except ValueError:
            print("Please enter a number (or leave blank if allowed).")

def evaluate_market(question: str, yes_pct: float, no_pct: Optional[float], stake: float,
                    cfg: Config) -> Dict[str, float | str]:
    # Normalize and fix slight mismatches
    p_yes = _as_prob(yes_pct)
    p_no = 1.0 - p_yes if no_pct is None else _as_prob(no_pct)
    if abs((p_yes + p_no) - 1.0) > 1e-6:
        p_no = 1.0 - p_yes

    # Calibrated "true" probability that YES happens under the 90% rule
    if p_yes > p_no:            # majority says YES
        q_yes = cfg.majority_accuracy
    elif p_yes < p_no:          # majority says NO
        q_yes = 1.0 - cfg.majority_accuracy
    else:                       # exact tie → 50/50
        q_yes = 0.5

    # EV per $1 for each side (price == market probability)
    ev_per_yes = q_yes - p_yes
    ev_per_no  = (1.0 - q_yes) - p_no

    # Choose side by EV
    if ev_per_yes < cfg.min_ev and ev_per_no < cfg.min_ev:
        side = "HOLD"
        chosen_price = None
        chosen_q_win = None
        ev_dollars = 0.0
        win_payout = 0.0
        lose_payout = 0.0
    elif ev_per_yes >= ev_per_no:
        side = "YES"
        c = p_yes
        chosen_price = c
        chosen_q_win = q_yes
        # If you spend 'stake' dollars, you buy stake/c shares.
        # If you win, payout = shares * $1 minus fee on winnings if fee_rate > 0.
        shares = stake / c if c > 0 else 0.0
        gross_win_payout = shares * 1.0
        fee = cfg.fee_rate * (gross_win_payout - stake)  # fee applied to profits; tweak if exchange differs
        win_payout = gross_win_payout - fee              # total returned to you (includes your stake)
        lose_payout = 0.0
        ev_dollars = (chosen_q_win * win_payout) + ((1.0 - chosen_q_win) * lose_payout) - stake
    else:
        side = "NO"
        c = p_no
        chosen_price = c
        chosen_q_win = 1.0 - q_yes  # prob NO is the correct outcome
        shares = stake / c if c > 0 else 0.0
        gross_win_payout = shares * 1.0
        fee = cfg.fee_rate * (gross_win_payout - stake)
        win_payout = gross_win_payout - fee
        lose_payout = 0.0
        ev_dollars = (chosen_q_win * win_payout) + ((1.0 - chosen_q_win) * lose_payout) - stake

    return {
        "question": question,
        "p_yes_market": round(p_yes, 4),
        "p_no_market": round(p_no, 4),
        "q_yes_calibrated": round(q_yes, 4),
        "side": side,
        "stake": round(stake, 2),
        "chosen_price": None if chosen_price is None else round(chosen_price, 4),
        "win_prob_of_chosen": None if chosen_q_win is None else round(chosen_q_win, 4),
        "ev_per_$": round(max(ev_per_yes, ev_per_no), 4) if side != "HOLD" else 0.0,
        "ev_dollars": round(ev_dollars, 2),
        "win_payout_if_correct": round(win_payout, 2),
        "lose_payout_if_wrong": round(lose_payout, 2),
    }

def main():
    print("=== Majority-Right-90% Interactive Evaluator ===")
    # Quick config (press Enter to accept defaults)
    a = _parse_float("Majority accuracy (default 0.90): ", allow_blank=True)
    m = _parse_float("Min EV per $ to bet (default 0.0): ", allow_blank=True)
    f = _parse_float("Fee rate on profits (default 0.0, e.g., 0.02 for 2%): ", allow_blank=True)
    cfg = Config(
        majority_accuracy=0.90 if a is None else a,
        min_ev=0.0 if m is None else m,
        fee_rate=0.0 if f is None else f,
    )

    print("\nEnter markets. Leave question blank to finish.")
    print("You can enter Yes% as 62 or 0.62; No% optional (will assume 1-Yes%).\n")

    results: List[Dict[str, float | str]] = []
    while True:
        q = input("Question: ").strip()
        if q == "":
            break
        y = _parse_float("  Yes % (e.g., 62 or 0.62): ")
        n = _parse_float("  No %  (optional, press Enter to use 1-Yes): ", allow_blank=True)
        stake = _parse_float("  Stake ($ to spend on recommended side): ")
        res = evaluate_market(q, y, n, stake, cfg)
        results.append(res)
        print(f"  → Side: {res['side']}, EV$: {res['ev_dollars']}, "
              f"Win payout if correct: ${res['win_payout_if_correct']}, Lose payout: ${res['lose_payout_if_wrong']}\n")

    if not results:
        print("\nNo markets entered. Bye!")
        return

    # Totals
    total_stake = sum(r["stake"] for r in results)  # type: ignore
    total_ev = sum(r["ev_dollars"] for r in results)  # type: ignore
    total_win_all = 0.0
    all_hold = True
    for r in results:
        if r["side"] != "HOLD":
            all_hold = False
            total_win_all += r["win_payout_if_correct"]  # type: ignore
        else:
            # If HOLD, you aren't staking; ignore in "all win" scenario
            pass

    print("\n=== Summary ===")
    for r in results:
        print(f"- {r['question']}\n"
              f"    market Yes={r['p_yes_market']} No={r['p_no_market']}  "
              f"calib q_yes={r['q_yes_calibrated']}  → {r['side']}  stake=${r['stake']}  "
              f"EV$={r['ev_dollars']}  win_payout=${r['win_payout_if_correct']}")
    print(f"\nTotal stake: ${round(total_stake,2)}")
    print(f"Total expected profit (sum EV): ${round(total_ev,2)}")
    if not all_hold:
        print(f"If ALL your recommended picks win: total payout back to you = ${round(total_win_all,2)}")
        print(f"Profit in that all-win scenario = ${round(total_win_all - total_stake, 2)}")
    print("\nDone.")

if __name__ == "__main__":
    main()

