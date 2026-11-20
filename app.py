"""BVGEC Solar Project Calculator.

A mortgage-calculator-style front end for BVGEC's project financial model.
Change an assumption on the left and every result updates.

Run it with:  streamlit run app.py

Formulas follow "BVGEC inputs and outputs based on the Excel projection".
Defaults come from BVGEC_modelAssumptions.csv when that file sits next to
this one, otherwise from the values below.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------- defaults
DEFAULTS = {
    # Financing
    "investor": 315344.0,     # Capital Investor ($)
    "has_loan": False,
    "loan": 0.0,              # Capital Loan ($)
    "loan_rate": 6.0,         # Loan Interest Rate (%)
    "loan_years": 20,         # Loan Repayment Period (years)
    "has_grant": False,
    "grant": 0.0,             # Capital Grant ($)
    "div_rate": 3.0,          # Investor Dividend Rate (%)
    "div_start": 3,           # Dividend Year Start
    "ret_pct": 10.0,          # Investor Capital Return (% of original capital per year)
    "ret_start": 4,           # Capital Return Year Start
    # Production and revenue
    "out": 239628.0,          # Initial Panel Output (kWh/yr)
    "out_adj": 0.0,           # Actual output vs the assumption (%), scenario only
    "rate": 0.135,            # Energy Rate ($/kWh)
    "rate_esc": 0.0,          # Optional energy rate increase (%/yr); not in the Excel spec
    "deg": 0.5,               # Panel Degradation (%/yr)
    "life": 25,               # Panel Life (years)
    "ecarb": 0.0,             # eCarbon "Production" (tCO2e/MWh)
    "cr0": 65.0,              # Initial Carbon Credit Rate ($/tCO2e)
    "cr_step": 15.0,          # Carbon credit rate increase per year ($)
    "itc_on": False,          # Claim the Clean Tech ITC
    "itc_pct": 30.0,          # Clean Tech ITC (% of investor capital, year 2)
    # Operating costs (% of capital cost total per year)
    "maint": 0.7,
    "admin": 0.4,
    "ins": 0.0,
    "removal": 0.0,
    "infl": 2.0,              # Escalation / Inflation (%/yr), applied to operating costs
    # Timeline and valuation
    "start_year": 2026,
    "end_year": 2050,
    "months_y1": 12,          # Months of operation in year 1
    "disc": 2.0,              # Discount rate for NPV (%). Not in the Excel spec: confirm with BVGEC
}

# Assumption names in BVGEC_modelAssumptions.csv -> (key, multiplier to the app's units)
CSV_MAP = {
    "capital investor": ("investor", 1), "capital loan": ("loan", 1),
    "capital grant": ("grant", 1), "loan interest rate": ("loan_rate", 100),
    "loan repayment period": ("loan_years", 1), "investor dividend rate": ("div_rate", 100),
    "dividend year start": ("div_start", 1), "investor capital return": ("ret_pct", 100),
    "capital return year start": ("ret_start", 1), "initial panel output": ("out", 1),
    "ecarbon": ("ecarb", 1), "energy rate": ("rate", 1),
    "initial carbon credit rate": ("cr0", 1), "panel degradation": ("deg", 100),
    "panel life": ("life", 1), "escalation": ("infl", 100), "inflation": ("infl", 100),
    "maintenance": ("maint", 100), "administration": ("admin", 100),
    "insurance": ("ins", 100), "panel removal": ("removal", 100),
}
INT_KEYS = {"loan_years", "div_start", "ret_start", "life", "start_year", "end_year", "months_y1"}


@st.cache_data
def load_defaults():
    """Start from DEFAULTS, then overwrite with BVGEC_modelAssumptions.csv if present."""
    d, source = dict(DEFAULTS), "built-in values"
    path = Path(__file__).with_name("BVGEC_modelAssumptions.csv")
    if path.exists():
        df = pd.read_csv(path)
        df["value"] = pd.to_numeric(df["value"].astype(str).str.replace(",", ""), errors="coerce")
        for _, row in df.dropna(subset=["value"]).iterrows():
            name = str(row["assumption"]).strip().lower()
            for pattern, (key, mult) in CSV_MAP.items():
                if name.startswith(pattern) or pattern in name:
                    val = row["value"] * mult
                    d[key] = int(round(val)) if key in INT_KEYS else float(val)
                    break
        d["has_loan"], d["has_grant"] = d["loan"] > 0, d["grant"] > 0
        source = "BVGEC_modelAssumptions.csv"
    return d, source


# ---------------------------------------------------------------- calculation engine
def loan_payment(amount, rate, years):
    """Annual payment on an amortising loan (Excel PMT)."""
    if amount <= 0 or years <= 0:
        return 0.0
    if rate == 0:
        return amount / years
    return amount * rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def irr(flows, lo=-0.99, hi=1.0):
    """Internal rate of return by bisection; None if it doesn't cross zero."""
    f = lambda r: sum(c / (1 + r) ** t for t, c in enumerate(flows))
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if flo * fm <= 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def calculate(p):
    """Run the year-by-year projection. `p` uses the app's units (percent as 0-100)."""
    pct = lambda k: p[k] / 100.0
    loan = p["loan"] if p["has_loan"] else 0.0
    grant = p["grant"] if p["has_grant"] else 0.0
    capex = p["investor"] + loan + grant                        # Capital Cost Total
    pmt = loan_payment(loan, pct("loan_rate"), int(p["loan_years"]))
    out = p["out"] * (1 + pct("out_adj"))                      # scenario adjustment
    n_years = int(p["end_year"]) - int(p["start_year"]) + 1

    loan_left, inv_left = loan, p["investor"]
    cum_profit, cum_re = 0.0, 0.0
    prod_prev, cr_prev = 0.0, p["cr0"]
    rows = []
    for n in range(1, n_years + 1):
        portion = p["months_y1"] / 12 if n == 1 else 1.0
        if n > int(p["life"]):
            prod = 0.0
        elif n <= 2:
            prod = out * portion                                 # years 1 and 2
        else:
            prod = prod_prev * (1 - pct("deg"))                  # year 3 onward
        prod_prev = prod if prod > 0 else prod_prev
        carbon_t = prod * p["ecarb"] / 1000
        cr = p["cr0"] if n == 1 else cr_prev + p["cr_step"]
        cr_prev = cr

        live = n <= int(p["life"])
        esc = (1 + pct("infl")) ** (n - 1)
        maint = capex * pct("maint") * portion * esc if live else 0.0
        admin = capex * pct("admin") * portion * esc if live else 0.0
        ins = capex * pct("ins") * portion * esc if live else 0.0
        removal = capex * pct("removal") * portion * esc if live else 0.0
        opex = maint + admin + ins + removal

        energy = prod * p["rate"] * (1 + pct("rate_esc")) ** (n - 1)
        carbon = carbon_t * cr
        itc = p["investor"] * pct("itc_pct") if (p["itc_on"] and n == 2) else 0.0
        revenue = energy + carbon + itc
        profit = revenue - opex

        interest = loan_left * pct("loan_rate") if loan_left > 0 else 0.0
        principal = min(loan_left, pmt - interest) if loan_left > 0 else 0.0
        loan_left -= principal
        dividend = inv_left * pct("div_rate") if n >= p["div_start"] else 0.0
        repay = min(inv_left, p["investor"] * pct("ret_pct")) if n >= p["ret_start"] else 0.0
        inv_left -= repay

        retained = profit - interest - principal - dividend - repay
        cum_re += retained
        cum_profit += profit
        rows.append({
            "Year": int(p["start_year"]) + n - 1, "Year #": n,
            "Energy production (kWh)": prod, "Carbon offset (tCO2e)": carbon_t,
            "Maintenance": maint, "Administration": admin, "Insurance": ins,
            "Panel removal": removal, "Total opex": opex,
            "Energy revenue": energy, "Carbon credit rate": cr, "Carbon credits": carbon,
            "Clean Tech ITC": itc, "Total revenue": revenue,
            "Revenue per kWh": revenue / prod if prod else 0.0,
            "Profit / loss": profit, "Loan interest": interest, "Loan principal": principal,
            "Investor dividend": dividend, "Investor capital repayment": repay,
            "Investor capital remaining": inv_left, "Retained earnings": retained,
            "Cumulative retained earnings": cum_re, "Cumulative profit": cum_profit,
        })
    t = pd.DataFrame(rows)

    d = pct("disc")
    npv = -capex + sum(v / (1 + d) ** (i + 1) for i, v in enumerate(t["Profit / loss"]))
    payback = next((int(y) for y, c in zip(t["Year #"], t["Cumulative profit"]) if c >= capex), None)
    neg = t[t["Cumulative retained earnings"] < 0]
    investor_flows = [-p["investor"]] + list(t["Investor dividend"] + t["Investor capital repayment"])
    return {
        "table": t, "capex": capex, "loan_payment": pmt, "npv": npv,
        "irr": irr([-capex] + list(t["Profit / loss"])),
        "investor_irr": irr(investor_flows) if p["investor"] > 0 else None,
        "payback": payback,
        "total_profit": t["Retained earnings"].sum(),
        "min_cum_re": t["Cumulative retained earnings"].min(),
        "first_short_year": int(neg["Year"].iloc[0]) if len(neg) else None,
        "lifetime_kwh": t["Energy production (kWh)"].sum(),
    }


def breakeven_output(p):
    """Initial panel output (kWh/yr) at which NPV reaches $0, other inputs unchanged."""
    q = dict(p, out_adj=0.0)
    f = lambda o: calculate(dict(q, out=o))["npv"]
    lo, hi = 0.0, p["out"] * 3
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


# ---------------------------------------------------------------- shared helpers
money = lambda v: f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"
md = lambda text: text.replace("$", "\\$")   # stop Markdown reading $ as maths


def tornado_table(p):
    """Each key assumption moved 10% down and up; returns NPV at each end and the swing."""
    swing = [("out", "Panel output"), ("rate", "Energy rate"), ("investor", "Capital from investors"),
             ("deg", "Panel degradation"), ("maint", "Maintenance"), ("admin", "Administration"),
             ("infl", "Escalation / inflation"), ("disc", "Discount rate")]
    rows = []
    for key, label in swing:
        lo = calculate(dict(p, **{key: p[key] * 0.9}))["npv"]
        hi = calculate(dict(p, **{key: p[key] * 1.1}))["npv"]
        rows.append({"Assumption": label, "NPV at −10%": lo, "NPV at +10%": hi, "Swing": abs(hi - lo)})
    return pd.DataFrame(rows).sort_values("Swing", ascending=False)


def breakeven_rate(p):
    """Energy rate ($/kWh) at which NPV reaches $0, other inputs unchanged."""
    f = lambda r: calculate(dict(p, rate=r))["npv"]
    lo, hi = 0.0, max(p["rate"] * 5, 1.0)
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


# ---------------------------------------------------------------- the "Ask the model" assistant
# Rule-based: it reads a question, changes the assumptions on a COPY of the inputs and runs the
# same calculate() as the rest of the app. It never makes up a number, and every answer lists
# the steps it took.
import re

# key -> (label, kind, lowest allowed, highest allowed): the same limits as the sidebar inputs
FIELDS = {
    "out_adj": ("Actual output vs assumption (%)", "float", -40.0, 20.0),
    "out": ("Initial panel output (kWh/yr)", "float", 1.0, 1e9),
    "rate": ("Energy rate ($/kWh)", "float", 0.0, 5.0),
    "rate_esc": ("Energy rate increase (%/yr)", "float", 0.0, 20.0),
    "deg": ("Panel degradation (%/yr)", "float", 0.0, 20.0),
    "life": ("Panel life (years)", "int", 1, 60),
    "ecarb": ("eCarbon (tCO2e/MWh)", "float", 0.0, 5.0),
    "cr0": ("Initial carbon credit rate ($/tCO2e)", "float", 0.0, 1000.0),
    "itc_on": ("Claim the Clean Tech ITC", "bool", None, None),
    "itc_pct": ("Clean Tech ITC (% of investor capital)", "float", 0.0, 100.0),
    "investor": ("Capital from investors ($)", "float", 1.0, 1e9),
    "has_loan": ("The project has a loan", "bool", None, None),
    "loan": ("Loan amount ($)", "float", 0.0, 1e9),
    "loan_rate": ("Loan interest rate (%)", "float", 0.0, 100.0),
    "loan_years": ("Loan repayment period (years)", "int", 1, 60),
    "div_rate": ("Investor dividend rate (%)", "float", 0.0, 50.0),
    "div_start": ("Dividends start in year #", "int", 1, 60),
    "ret_pct": ("Investor capital returned (% per year)", "float", 0.0, 100.0),
    "ret_start": ("Capital return starts in year #", "int", 1, 60),
    "maint": ("Maintenance (%)", "float", 0.0, 20.0),
    "admin": ("Administration (%)", "float", 0.0, 20.0),
    "ins": ("Insurance (%)", "float", 0.0, 20.0),
    "removal": ("Panel removal (%)", "float", 0.0, 20.0),
    "infl": ("Escalation / inflation (%/yr)", "float", 0.0, 20.0),
    "disc": ("Discount rate for NPV (%)", "float", 0.0, 50.0),
}
FRACTION_KEYS = {"disc", "div_rate", "loan_rate", "ret_pct", "infl", "itc_pct"}   # 0.06 means 6%

WHY = {
    "out_adj": "Revenue is production × energy rate, so lower output cuts revenue every year while operating costs stay the same.",
    "out": "Revenue is production × energy rate, so more or less output moves revenue every year while operating costs stay the same.",
    "rate": "The energy rate is the price paid per kWh, so it scales revenue directly.",
    "rate_esc": "A yearly rate increase makes each year's revenue larger than the year before. The Excel projection has no such increase.",
    "deg": "Degradation lowers production each year from year 3, so the loss compounds over the panel life.",
    "life": "Panels earn revenue only during their life, so a longer or shorter life adds or removes years of profit.",
    "ecarb": "eCarbon is the carbon avoided per MWh. At 0, carbon credits earn nothing.",
    "cr0": "The carbon credit rate sets what each tonne of avoided carbon earns, but only if eCarbon is above 0.",
    "itc_on": "The Clean Tech ITC is a one-time payment in year 2, which adds to revenue that year.",
    "itc_pct": "The ITC is a percentage of investor capital, paid in year 2.",
    "investor": "Capital cost sets the size of the investment, and operating costs are a share of it each year.",
    "has_loan": "A loan adds to the capital cost and creates yearly interest and principal payments.",
    "loan": "A loan adds to the capital cost and creates yearly interest and principal payments.",
    "loan_rate": "Interest is charged on the loan balance each year and reduces retained earnings.",
    "loan_years": "A longer repayment period lowers each yearly loan payment.",
    "div_rate": "Dividends are paid on investor capital still outstanding, so they reduce retained earnings (not NPV in BVGEC's formula).",
    "div_start": "Dividends only begin in this year.",
    "ret_pct": "Capital returned each year is paid out of project earnings, so a faster return drains retained earnings sooner.",
    "ret_start": "Returning capital later keeps more cash in the project in the early years.",
    "maint": "Maintenance is a percentage of capital cost, every year, rising with inflation.",
    "admin": "Administration is a percentage of capital cost, every year, rising with inflation.",
    "ins": "Insurance is a percentage of capital cost, every year, rising with inflation.",
    "removal": "Panel removal is a percentage of capital cost, every year, rising with inflation.",
    "infl": "Inflation raises operating costs each year (in the calculator it applies to costs only).",
    "disc": "A higher discount rate shrinks future profit more, so NPV falls.",
}

EXAMPLES = [
    "What if output drops 6%?",
    "What if the discount rate is 6%?",
    "Why do retained earnings go below $0?",
    "How low can output go before NPV hits $0?",
    "What moves NPV the most?",
    "What would you suggest?",
]

NEG = re.compile(r"\b(drop\w*|fall\w*|fell|less|lower\w*|below|decreas\w*|reduc\w*|down|short\w*|under(?:perform\w*)?|minus|cut\w*|declin\w*|smaller|worse)\b")
POS = re.compile(r"\b(increas\w*|up|higher|more|above|rise\w*|rising|rose|boost\w*|grow\w*|raise\w*|better|bigger|improv\w*)\b")


def _years(clause):
    """Pull out 'from year 6' (start year) and '15 years' (duration) so they aren't read as values."""
    start = re.search(r"(?:from|starting|starts?|begin\w*|in)\s+year\s*#?\s*(\d+)", clause)
    dur = re.search(r"(\d+)\s*(?:years?|yrs?)\b", clause)
    cleaned = clause
    for m in (start, dur):
        if m:
            cleaned = cleaned.replace(m.group(0), " ")
    return (int(start.group(1)) if start else None), (int(dur.group(1)) if dur else None), cleaned


def _nums(clause):
    """Numbers in a clause as (value, unit); unit is '%', 'k', 'm', 'kwh', 'mwh', 'c' (cents) or ''."""
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(%|percent|kwh|mwh|k\b|m\b|cents?\b)?", clause):
        v = float(m.group(1).replace(",", ""))
        u = (m.group(2) or "").lower()
        out.append((v, {"percent": "%", "cent": "c", "cents": "c"}.get(u, u)))
    return out


def _direction(clause):
    n, p = bool(NEG.search(clause)), bool(POS.search(clause))
    return -1 if n and not p else (1 if p and not n else 0)


def parse_changes(question, p):
    """Turn a question into {assumption: new value}. Returns (changes, notes, matched)."""
    q = question.lower().replace("$", " ").replace("’", "'")
    changes, notes, matched = {}, [], []

    def put(key, value, phrase):
        label, kind, lo, hi = FIELDS[key]
        if kind == "bool":
            changes[key] = bool(value)
        else:
            value = int(round(value)) if kind == "int" else float(value)
            if value < lo or value > hi:
                notes.append(f"I ignored {label} = {value:g}: the calculator accepts {lo:g} to {hi:g}.")
                return
            changes[key] = value
        matched.append((phrase.strip(), key))

    for clause in re.split(r"(?<!\d),|,(?!\d)|;|\band\b|\bthen\b|\balso\b|\bbut\b", q):
        c = clause.strip()
        if not c:
            continue
        yr_start, yr_dur, cc = _years(c)
        nums = _nums(cc)
        d = _direction(cc)
        pcts = [v for v, u in nums if u == "%"]
        plain = [v for v, u in nums if u in ("", "c")]
        val = pcts[0] if pcts else (plain[0] if plain else None)
        amounts = [v * (1000 if u == "k" else 1e6 if u == "m" else 1)
                   for v, u in nums if u in ("k", "m") or (u == "" and v >= 1000)]
        yearly = re.search(r"per year|a year|yearly|annual|each year|every year|/yr|/year", cc)

        def pct_key(key):
            """Percent-type value; read 0.06 as 6% (and say so)."""
            if val is None:
                return False
            v = val
            if key in FRACTION_KEYS and not pcts and 0 < v <= 0.3:
                v *= 100
                notes.append(f"I read {val:g} as {v:g}% for {FIELDS[key][0]}.")
            put(key, v, c)
            return True

        if "discount" in cc:
            if pct_key("disc"):
                continue
        if re.search(r"\bloan|borrow|\bdebt", cc):
            before = len(changes)
            if amounts:
                put("loan", amounts[0], c); put("has_loan", True, c)
            if pcts:
                put("loan_rate", pcts[0], c)
            if yr_dur:
                put("loan_years", yr_dur, c)
            if len(changes) > before:
                continue
        if "dividend" in cc:
            if pct_key("div_rate"):
                if yr_start:
                    put("div_start", yr_start, c)
                continue
            if yr_start:
                put("div_start", yr_start, c)
                continue
        if re.search(r"(capital|investor|investment).*(return|repay|paid back)|(return|repay).*(capital|investor)", cc):
            before = len(changes)
            pct_key("ret_pct")
            if yr_start:
                put("ret_start", yr_start, c)
            if len(changes) > before:
                continue
        if re.search(r"energy|price|rate", cc) and yearly and re.search(r"increas|escalat|grow|rise|rising", cc) \
                and not re.search(r"inflation|cost|maint|admin", cc):
            if val is not None:
                put("rate_esc", val, c)
                continue
        if re.search(r"inflation|escalation", cc):
            if pct_key("infl"):
                continue
        if "degrad" in cc:
            if pct_key("deg"):
                continue
        for word, key in (("maint", "maint"), ("admin", "admin"), ("insur", "ins"),
                          ("removal", "removal"), ("decommission", "removal")):
            if word in cc and pct_key(key):
                break
        else:
            key = None
        if key:
            continue
        if re.search(r"\bitc\b|clean ?tech|tax credit", cc):
            if pcts:
                put("itc_pct", pcts[0], c); put("itc_on", True, c)
            else:
                put("itc_on", not re.search(r"\b(no|not|without|off|exclude|skip|remove)\b", cc), c)
            continue
        if re.search(r"ecarbon|carbon offset|tco2e/mwh|carbon avoided", cc):
            if val is not None:
                put("ecarb", val, c)
                continue
        if "carbon" in cc and re.search(r"credit|price|rate", cc) and val is not None:
            put("cr0", val, c)
            continue
        if re.search(r"panel life|lifetime|\blife\b|\blast\b|years of operation", cc) and (yr_dur or val):
            put("life", yr_dur or val, c)
            continue
        if re.search(r"over-?estimat", cc) and pcts:
            o = pcts[0] / 100
            put("out_adj", -100 * (1 - 1 / (1 + o)), c)
            notes.append(f"An over-estimate of {pcts[0]:g}% means actual output is {100 * (1 - 1 / (1 + o)):.1f}% below the assumption.")
            continue
        if re.search(r"like 2025|2025 gap|our eda|eda gap|practice data|observed gap", cc):
            put("out_adj", -6.0, c)
            notes.append("I used −6%, the 2025 gap from our EDA on practice data (6.4% over-estimate).")
            continue
        if re.search(r"energy (rate|price)|price per kwh|per kwh|kwh price|tariff|\brate\b|\bprice\b", cc) \
                and re.search(r"energy|kwh|\$|price|tariff", c):
            if pcts:
                sign = d or -1
                if not d:
                    notes.append(f"I read the {pcts[0]:g}% change in the energy rate as lower; say 'higher' for the other direction.")
                put("rate", p["rate"] * (1 + sign * pcts[0] / 100), c)
                continue
            if plain:
                put("rate", plain[0] / 100 if ("c" in [u for _, u in nums] or plain[0] >= 1) else plain[0], c)
                continue
        if re.search(r"output|production|generation|generat|produce|kwh|performance|shortfall", cc):
            big = [v * (1000 if u == "k" else 1) * (1000 if u == "mwh" else 1)
                   for v, u in nums if u in ("kwh", "mwh", "k") or (u == "" and v >= 1000)]
            if big:
                put("out", big[0], c)
                continue
            if pcts:
                sign = d or -1
                if not d:
                    notes.append(f"I read the {pcts[0]:g}% change in output as lower; say 'higher' for the other direction.")
                put("out_adj", sign * pcts[0], c)
                continue
        if re.search(r"investor capital|capital cost|capex|investment|capital from", cc) and amounts:
            put("investor", amounts[0], c)
            continue
    return changes, notes, matched


def _fmt(key, v):
    kind = FIELDS[key][1]
    if kind == "bool":
        return "Yes" if v else "No"
    if key in ("out", "investor", "loan"):
        return f"{v:,.0f}"
    if key == "rate":
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:g}"


def _change_table(p0, changes):
    return pd.DataFrame([{"Assumption": FIELDS[k][0], "Now": _fmt(k, p0[k]), "Asked": _fmt(k, v)}
                         for k, v in changes.items()])


def _compare_table(a, b):
    def row(name, x, y, fmt, diff=True):
        return {"Result": name, "Now": fmt(x), "Asked": fmt(y),
                "Change": fmt_diff(x, y, fmt) if diff else ""}

    def fmt_diff(x, y, fmt):
        if x is None or y is None:
            return ""
        d = y - x
        return ("+" if d > 0 else "") + fmt(d) if d else "no change"
    yr = lambda v: f"Year {v}" if v else "Not within the analysis"
    pc = lambda v: f"{v:.2%}" if v is not None else "n/a"
    rows = [row("Net present value", a["npv"], b["npv"], money),
            row("Project IRR", a["irr"], b["irr"], pc),
            {"Result": "Payback", "Now": yr(a["payback"]), "Asked": yr(b["payback"]), "Change": ""},
            {"Result": "Retained earnings first below $0",
             "Now": str(a["first_short_year"] or "never"), "Asked": str(b["first_short_year"] or "never"), "Change": ""},
            row("Investor IRR", a["investor_irr"], b["investor_irr"], pc),
            row("Total profit, all years", a["total_profit"], b["total_profit"], money)]
    return pd.DataFrame(rows)


def _answer(text, steps, changes=None, tables=None, options=None, followups=None):
    return {"text": text, "steps": steps, "changes": changes or {}, "tables": tables or [],
            "options": options or [], "followups": followups or []}


CALC_NOTE = ("Every number comes from the same calculate() function the calculator uses, following "
             "BVGEC's Excel projection. I don't estimate or guess any figure.")
ASSUMED = ("Remember: the energy data is practice data, and the 2% discount rate is our placeholder "
           "(BVGEC hasn't given one).")


SHORT = {"out_adj": lambda v: f"output {v:+.1f}% vs the assumption",
         "rate": lambda v: f"energy rate ${v:.3f}/kWh",
         "disc": lambda v: f"discount rate {v:g}%"}


def _assume_line(changes, p0):
    bits = [SHORT[k](v) if k in SHORT else f"{FIELDS[k][0].lower()} = {_fmt(k, v)}"
            for k, v in changes.items() if k != "has_loan"]
    return "Assuming " + ", ".join(bits) + ": "


def answer_what_if(p0, changes, notes, matched):
    p1 = dict(p0, **changes)
    a, b = calculate(p0), calculate(p1)
    dn = b["npv"] - a["npv"]
    if abs(dn) < 0.5:
        parts = [f"**NPV doesn't change: it stays at {money(a['npv'])}.**"]
    else:
        parts = [f"**NPV {'falls' if dn < 0 else 'rises'} from {money(a['npv'])} to {money(b['npv'])} "
                 f"({'+' if dn > 0 else ''}{money(dn)}).**"]
    if a["payback"] != b["payback"]:
        parts.append(f"Payback moves from {('year %d' % a['payback']) if a['payback'] else 'not within the analysis'} "
                     f"to {('year %d' % b['payback']) if b['payback'] else 'not within the analysis'}.")
    if a["first_short_year"] != b["first_short_year"]:
        was = f"first below $0 in {a['first_short_year']}" if a["first_short_year"] else "never below $0"
        if b["first_short_year"]:
            parts.append(f"Retained earnings first go below $0 in {b['first_short_year']} (before: {was.replace('first below $0 in ', '')}).")
        else:
            parts.append(f"Retained earnings now stay above $0 every year (before: {was}).")
    elif b["first_short_year"]:
        parts.append(f"Retained earnings still go below $0 in {b['first_short_year']}.")
    if b["npv"] < 0:
        parts.append("With NPV below $0, the project would cost more than it is worth today.")
    elif a["npv"] < 0 <= b["npv"]:
        parts.append("That turns NPV positive.")
    follow = ["What would you suggest?"]
    if b["npv"] >= 0:
        follow.insert(0, "How low can output go before NPV hits $0?")
    else:
        follow.insert(0, "What moves NPV the most?")
    steps = ["What I understood: " + "; ".join(f"“{ph}” → {FIELDS[k][0]}" for ph, k in matched) + ".",
             "I ran the calculator twice: once with the current settings and once with the changes above.",
             "Why it matters: " + " ".join(dict.fromkeys(WHY[k] for k in changes if k in WHY and k != "has_loan")),
             CALC_NOTE, ASSUMED] + notes
    return _answer(" ".join(parts), steps, changes,
                   [("What changed", _change_table(p0, changes)), ("Results before and after", _compare_table(a, b))],
                   followups=follow)


def answer_breakeven(p, changes, question):
    p1 = dict(p, **changes)
    pre = _assume_line(changes, p) if changes else ""
    if re.search(r"discount", question):
        r = calculate(p1)["irr"]
        text = (f"{pre}NPV reaches $0 at a discount rate of **{r:.2%}**, which is the project IRR. At any higher discount "
                "rate, the project looks like it costs more than it is worth." if r is not None else "The IRR can't be calculated.")
        steps = ["NPV equals $0 exactly when the discount rate equals the project IRR.", CALC_NOTE]
        return _answer(text, steps, changes, followups=["What if the discount rate is 6%?", "What would you suggest?"])
    if re.search(r"\brate\b|price|tariff|per kwh", question) and "output" not in question:
        be = breakeven_rate(p1)
        if be is None:
            return _answer("NPV doesn't cross $0 for any energy rate I tried.", [CALC_NOTE])
        text = (f"{pre}NPV reaches $0 when the energy rate falls to **${be:.3f} per kWh**, "
                f"{1 - be / p1['rate']:.0%} below the current ${p1['rate']:.3f}.")
        steps = ["I tried energy rates between $0 and five times the current rate, narrowing in 60 times on the one where NPV is $0.",
                 CALC_NOTE, ASSUMED]
        return _answer(text, steps, changes, followups=["How low can output go before NPV hits $0?", "What would you suggest?"])
    be = breakeven_output(p1)
    if be is None:
        return _answer("NPV doesn't cross $0 for any output I tried.", [CALC_NOTE])
    margin = 1 - be / p1["out"]
    text = (f"{pre}NPV reaches $0 at **{be:,.0f} kWh a year**, {margin:.0%} below the {p1['out']:,.0f} kWh assumption. "
            f"Our EDA on practice data found about 6% less output than assumed in 2025, so there is "
            f"{'a comfortable' if margin > 0.15 else 'a thin'} margin.")
    steps = ["I tried outputs between 0 and three times the assumption, narrowing in 60 times on the one where NPV is $0.",
             CALC_NOTE, ASSUMED]
    return _answer(text, steps, changes, followups=["What if output drops 6%?", "What would you suggest?"])


def answer_drivers(p, changes):
    p1 = dict(p, **changes)
    sw = tornado_table(p1)
    top = sw.head(3)
    text = (_assume_line(changes, p) if changes else "") + "NPV is most sensitive to: " + "; ".join(
        f"**{r['Assumption']}** (±10% moves NPV by {money(r['Swing'])})" for _, r in top.iterrows()) + "."
    steps = ["I moved each key assumption 10% down and then 10% up, one at a time, and recorded NPV each time. "
             "The swing is the gap between the two.", CALC_NOTE]
    tbl = sw.copy()
    for c in ("NPV at −10%", "NPV at +10%", "Swing"):
        tbl[c] = tbl[c].map(money)
    return _answer(text, steps, changes, [("All assumptions, biggest swing first", tbl)],
                   followups=["What would you suggest?", "How low can output go before NPV hits $0?"])


def answer_cash(p, changes):
    p1 = dict(p, **changes)
    res = calculate(p1)
    t = res["table"]
    paid = t["Investor dividend"] + t["Investor capital repayment"]
    over = t[paid > t["Profit / loss"]]
    pre = _assume_line(changes, p) if changes else ""
    if not res["first_short_year"]:
        text = (pre + "Retained earnings stay above $0 every year, so the project can pay members from its own earnings. "
                f"The lowest cumulative point is {money(res['min_cum_re'])}.")
        return _answer(text, ["Retained earnings = profit − loan payments − dividends − capital returned, added up year by year.", CALC_NOTE],
                       changes, followups=["What would you suggest?", "What moves NPV the most?"])
    yrs = f"{int(over['Year'].min())} to {int(over['Year'].max())}" if len(over) else "some years"
    text = (f"{pre}Retained earnings are profit minus loan payments, dividends and capital returned to members. "
            f"In {yrs}, the project pays members more than it earns, so the running total falls and first goes below $0 in "
            f"**{res['first_short_year']}** (lowest point {money(res['min_cum_re'])}). "
            "NPV can still be positive, because NPV counts profit and not these payouts. "
            "Paying capital back more slowly fixes it.")
    show = t.loc[:, ["Year", "Profit / loss", "Investor dividend", "Investor capital repayment",
                     "Retained earnings", "Cumulative retained earnings"]].head(16).copy()
    for c in show.columns[1:]:
        show[c] = show[c].map(money)
    steps = ["Retained earnings = profit − loan interest − loan principal − dividend − capital returned.",
             "I compared each year's profit with what is paid to members (dividends + capital returned) and added up the difference.",
             CALC_NOTE]
    return _answer(text, steps, changes, [("First 16 years", show)],
                   followups=["What would you suggest?", "What if the capital return is 7% a year?"])


def answer_npv(p, changes):
    p1 = dict(p, **changes)
    res = calculate(p1)
    pv = res["npv"] + res["capex"]
    word = "positive" if res["npv"] >= 0 else "negative"
    text = (f"{_assume_line(changes, p) if changes else ''}NPV is **{money(res['npv'])}** ({word}). It adds up each year's profit, shrunk to today's "
            f"dollars at the {p1['disc']:g}% discount rate: that comes to {money(pv)}. The capital cost is {money(res['capex'])}, "
            f"so NPV = {money(pv)} − {money(res['capex'])}. The project earns back its cost and "
            f"{'more' if res['npv'] >= 0 else 'not quite all of it'} in today's dollars. "
            "A higher discount rate would shrink the first number and so reduce NPV.")
    steps = ["NPV = −capital cost + each year's profit ÷ (1 + discount rate) to the power of the year number.", CALC_NOTE,
             "The discount rate isn't in BVGEC's spreadsheet. 2% is our placeholder."]
    return _answer(text, steps, changes, followups=["What if the discount rate is 6%?", "What moves NPV the most?"])


def answer_term(term, p, changes):
    p1 = dict(p, **changes)
    res = calculate(p1)
    irr_txt = f"{res['irr']:.2%}" if res["irr"] is not None else "n/a"
    inv_txt = f"{res['investor_irr']:.2%}" if res["investor_irr"] is not None else "n/a"
    texts = {
        "payback": f"Payback is the first year when cumulative profit covers the capital cost of {money(res['capex'])}. Right now it is "
                   f"{('year %d' % res['payback']) if res['payback'] else 'not within the analysis period'}.",
        "irr": f"The project IRR is the yearly return the project earns on its capital cost. It is {irr_txt} now. If the discount rate is above the IRR, NPV is negative.",
        "investor irr": f"Investor IRR is the return to members from dividends and capital returned, compared with what they put in. It is {inv_txt} now.",
        "discount": f"The discount rate turns future dollars into today's dollars: $100 in 10 years is worth less than $100 now. The calculator uses {p1['disc']:g}%, which is our placeholder because BVGEC's spreadsheet doesn't give one.",
        "degrad": f"Degradation is how much less the panels produce each year, starting in year 3. The model assumes {p1['deg']:g}% a year.",
        "energy rate": f"The energy rate is the price the host site pays per kWh: ${p1['rate']:.3f} now. Revenue is production × this rate.",
        "dividend": f"The dividend is paid to investors each year on the capital still outstanding: {p1['div_rate']:g}% from year {p1['div_start']}.",
        "capital": f"Capital cost total is investor capital plus any loan and grant: {money(res['capex'])} now. Operating costs are a percentage of it each year.",
        "carbon": "Carbon credits earn money only when eCarbon (tCO2e avoided per MWh) is above 0. In BVGEC's inputs it is 0, so carbon credits earn $0 right now. This is worth confirming with BVGEC.",
        "itc": "The Clean Tech ITC is a one-time tax credit paid in year 2 as a share of investor capital. It is off by default; confirm with BVGEC whether the project qualifies.",
        "inflation": f"Inflation raises operating costs each year: {p1['infl']:g}% now. In the calculator it applies to costs only, not to the energy rate.",
        "retained": "Retained earnings are what is left of profit after loan payments, dividends and capital returned to members.",
    }
    steps = ["I matched your question to a plain-language definition and filled in the current numbers.", CALC_NOTE]
    return _answer(texts[term], steps, changes, followups=["What if output drops 6%?", "What moves NPV the most?"])


def answer_suggest(p, changes):
    p1 = dict(p, **changes)
    res = calculate(p1)
    items, options, tables = [], [], []
    steps = ["I tested a set of changes, one at a time, on a copy of the inputs, and kept the ones that help.", CALC_NOTE]
    # 1. cash timing
    if res["first_short_year"]:
        tests = [(f"Return {p1['ret_pct'] * 0.7:g}% of capital a year instead of {p1['ret_pct']:g}%", {"ret_pct": round(p1["ret_pct"] * 0.7, 2)}),
                 (f"Start returning capital in year {p1['ret_start'] + 2} instead of year {p1['ret_start']}", {"ret_start": p1["ret_start"] + 2}),
                 (f"Return {p1['ret_pct'] * 0.5:g}% of capital a year", {"ret_pct": round(p1["ret_pct"] * 0.5, 2)})]
        rows = []
        for label, ch in tests:
            r = calculate(dict(p1, **ch))
            rows.append({"Option": label, "Retained earnings first below $0": str(r["first_short_year"] or "never"),
                         "Lowest point": money(r["min_cum_re"]), "Investor IRR": f"{r['investor_irr']:.2%}" if r["investor_irr"] is not None else "n/a",
                         "NPV": money(r["npv"])})
            if r["first_short_year"] is None and len(options) < 2:
                options.append((f"Apply: {label}", dict(changes, **ch)))
        tables.append(("Slower capital repayment", pd.DataFrame(rows)))
        good = [r for r in rows if r["Retained earnings first below $0"] == "never"]
        if good:
            items.append(f"**Slow the capital repayment.** Today retained earnings go below $0 in {res['first_short_year']}. "
                         f"“{good[0]['Option']}” keeps them above $0 every year (lowest point {good[0]['Lowest point']}) and leaves NPV unchanged at {good[0]['NPV']}, "
                         "because BVGEC's NPV doesn't count payouts. The trade-off is members get their capital back later.")
        else:
            items.append("**Slow the capital repayment further or lower the dividend.** None of the options I tried kept retained earnings above $0.")
    # 2. downside margin
    be = breakeven_output(p1)
    if be:
        margin = 1 - be / p1["out"]
        items.append(f"**Plan for lower output.** NPV stays positive until output is {margin:.0%} below the assumption "
                     f"({be:,.0f} kWh a year). Our EDA found about 6% less than assumed in 2025 on practice data, so ask BVGEC for real readings to confirm the margin.")
    # 3. discount rate
    if res["irr"] is not None:
        n6 = calculate(dict(p1, disc=6.0))["npv"]
        items.append(f"**Confirm the discount rate.** NPV is {money(res['npv'])} at {p1['disc']:g}% but {money(n6)} at 6%, and turns negative above {res['irr']:.2%}. "
                     "A typical rate for a community energy project is higher than our 2% placeholder, so this is the most important number to get from BVGEC.")
    # 4. energy rate escalation
    if p1["rate_esc"] == 0:
        n2 = calculate(dict(p1, rate_esc=2.0))["npv"]
        items.append(f"**Ask whether the energy rate rises over time.** If it rose 2% a year (not in the Excel projection), NPV would be {money(n2)} "
                     f"instead of {money(res['npv'])}.")
    # 5. missing costs
    if p1["ins"] == 0:
        ni = calculate(dict(p1, ins=0.3))["npv"]
        items.append(f"**Check insurance.** It is 0% in the inputs. As an illustration, 0.3% of capital cost a year would move NPV to {money(ni)}.")
    if p1["ecarb"] == 0:
        items.append("**Check carbon credits.** eCarbon is 0, so the carbon credit rate earns nothing. Confirm whether BVGEC can claim credits.")
    text = (_assume_line(changes, p) if changes else "") + "Here is what I would look at, in order:\n\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(items[:5], 1))
    return _answer(text, steps, changes, tables, options, ["Why do retained earnings go below $0?", "What moves NPV the most?"])


def answer_summary(p, changes):
    p1 = dict(p, **changes)
    res = calculate(p1)
    irr_txt = f"{res['irr']:.2%}" if res["irr"] is not None else "n/a"
    text = (f"{_assume_line(changes, p) if changes else ''}NPV is **{money(res['npv'])}**, project IRR {irr_txt}, payback "
            f"{('in year %d' % res['payback']) if res['payback'] else 'not within the analysis'}. "
            + (f"Retained earnings go below $0 in {res['first_short_year']} (lowest {money(res['min_cum_re'])})."
               if res["first_short_year"] else "Retained earnings stay above $0 every year."))
    return _answer(text, [CALC_NOTE], changes, followups=["What would you suggest?", "What moves NPV the most?"])


def answer_help():
    text = ("I can answer questions about this project's financial model. Try:\n\n"
            "- **What-ifs:** “What if output drops 10% and the energy rate falls to 0.12?”\n"
            "- **Explain:** “Why do retained earnings go below $0?” or “What is the discount rate?”\n"
            "- **Limits:** “How low can output go before NPV hits $0?”\n"
            "- **Drivers:** “What moves NPV the most?”\n"
            "- **Suggestions:** “What would you suggest?”\n\n"
            "I use the same calculation as the calculator, and each answer has a “How I got this” section. "
            "For a what-if, you can apply the changes to the sliders with one click.")
    return _answer(text, ["I'm a rule-based assistant: I look for assumptions and numbers in your question and run the calculator. I'm not connected to any outside AI."])


def agent_answer(question, p, state=None):
    """Route a question to the right answer. `state` remembers the last what-if for follow-ups."""
    state = state if state is not None else {}
    q = question.lower().strip()
    changes, notes, matched = parse_changes(question, p)
    if re.match(r"(and|also|now|what about|how about|same)\b", q) and changes:
        changes = dict(state.get("agent_last", {}), **changes)
    intent = None
    for name, pat in (
        ("help", r"^(hi|hello|hey|help)\b|what can you|how do (i|you) (use|ask)|examples"),
        ("suggest", r"suggest|recommend|advice|advise|should (we|bvgec|they)|what can we do|mitigat|ideas|how (can|could|do) we"),
        ("breakeven", r"break.?even|how low|how far|how much (can|could)|lowest|minimum|before (npv|it)|tipping|threshold|zero|hits? \$?0|reach\w* \$?0|makes? npv"),
        ("drivers", r"most (important|sensitive|impact)|biggest|drivers?|sensitiv|tornado|what moves|which assumption|matters? most"),
        ("cash", r"retained|can'?t pay|cannot pay|pay (members|dividends)|below \$?0|cash|payout"),
        ("npv", r"why.*\bnpv\b|\bnpv\b.*(negative|positive|low|high|so)|explain.*\bnpv\b|what is (the )?\bnpv\b|what'?s (the )?\bnpv\b"),
        ("summary", r"summar|where are we|overview|status|how are we doing|current results|results so far"),
    ):
        if re.search(pat, q):
            intent = name
            break
    if intent is None and not changes and notes:
        return _answer(" ".join(notes), ["I understood the assumption but the calculator can't take that value."],
                       followups=EXAMPLES[:3])
    if intent is None and not changes:
        for term, pat in (("investor irr", r"investor irr|return to members"), ("irr", r"\birr\b|internal rate"),
                          ("payback", r"payback|pay back"), ("discount", r"discount"), ("degrad", r"degrad"),
                          ("energy rate", r"energy rate|energy price"), ("dividend", r"dividend"),
                          ("capital", r"capital cost|capex|capital"), ("carbon", r"carbon|ecarbon"),
                          ("itc", r"\bitc\b|clean ?tech|tax credit"), ("inflation", r"inflation|escalation"),
                          ("retained", r"retained")):
            if re.search(pat, q):
                intent = "term:" + term
                break
    if intent is None and changes:
        intent = "whatif"
    if intent is None:
        text = ("I couldn't match that to a calculation. I can run what-ifs (“what if output drops 10%?”), "
                "explain results (“why do retained earnings go below $0?”), find limits (“how low can output go?”) and make suggestions.")
        return _answer(text, ["I look for assumptions (output, energy rate, discount rate, degradation, costs, loan, dividends, capital return) and numbers in your question."],
                       followups=EXAMPLES[:3])
    if changes and intent not in ("help",):
        state["agent_last"] = changes
    if intent == "help":
        return answer_help()
    if intent == "whatif":
        return answer_what_if(p, changes, notes, matched)
    out = {"suggest": lambda: answer_suggest(p, changes), "breakeven": lambda: answer_breakeven(p, changes, q),
           "drivers": lambda: answer_drivers(p, changes), "cash": lambda: answer_cash(p, changes),
           "npv": lambda: answer_npv(p, changes), "summary": lambda: answer_summary(p, changes)}.get(intent)
    if out:
        ans = out()
    else:
        ans = answer_term(intent.split(":", 1)[1], p, changes)
    if changes and matched:
        ans["steps"].insert(0, "What I understood: " + "; ".join(f"“{ph}” → {FIELDS[k][0]}" for ph, k in matched) + ".")
    ans["steps"].extend(n for n in notes if n not in ans["steps"])
    return ans


# ---------------------------------------------------------------- page
st.set_page_config(page_title="BVGEC Solar Project Calculator", layout="wide")
defaults, defaults_source = load_defaults()

for k, v in defaults.items():
    st.session_state.setdefault(k, v)


def reset():
    for k, v in defaults.items():
        st.session_state[k] = v



with st.sidebar:
    st.header("Assumptions")
    st.caption(f"Defaults from {defaults_source}. Hover over ⓘ for an explanation.")
    st.button("Reset to BVGEC defaults", on_click=reset, width="stretch")

    with st.expander("Production and revenue", expanded=True):
        st.number_input("Initial panel output (kWh/yr)", min_value=0.0, step=1000.0,
                        format="%.0f", key="out",
                        help="Electricity the panels are expected to make in a full first year.")
        st.slider("Actual output vs assumption (%)", -40.0, 20.0, step=0.5, key="out_adj",
                  help="Scenario: what if real output differs from the assumption? Our EDA on "
                       "practice data found the sites produced about 6% less than assumed in 2025.")
        st.number_input("Energy rate ($/kWh)", min_value=0.0, step=0.005, format="%.3f", key="rate",
                        help="Price the host site pays for each kWh.")
        st.number_input("Energy rate increase (%/yr, optional)", min_value=0.0, step=0.5,
                        key="rate_esc", help="Not in the Excel projection. Leave at 0 to match it.")
        st.number_input("Panel degradation (%/yr)", min_value=0.0, step=0.1, key="deg",
                        help="How much less the panels produce each year from year 3.")
        st.number_input("Panel life (years)", min_value=1, step=1, key="life")
        st.number_input('eCarbon "production" (tCO2e/MWh)', min_value=0.0, step=0.05,
                        format="%.2f", key="ecarb",
                        help="Carbon avoided per MWh. At 0, carbon credits earn nothing.")
        st.number_input("Initial carbon credit rate ($/tCO2e)", min_value=0.0, step=5.0, key="cr0")
        st.number_input("Carbon credit rate increase ($/yr)", min_value=0.0, step=1.0, key="cr_step")
        st.checkbox("Claim the Clean Tech ITC (year 2)", key="itc_on",
                    help="Investment tax credit paid in year 2 as a share of investor capital. "
                         "Confirm eligibility with BVGEC.")
        if st.session_state["itc_on"]:
            st.number_input("Clean Tech ITC (% of investor capital)", min_value=0.0,
                            max_value=100.0, step=5.0, key="itc_pct")

    with st.expander("Financing"):
        st.number_input("Capital from investors ($)", min_value=0.0, step=5000.0,
                        format="%.0f", key="investor")
        st.checkbox("The project has a loan", key="has_loan")
        if st.session_state["has_loan"]:
            st.number_input("Loan amount ($)", min_value=0.0, step=5000.0, format="%.0f", key="loan")
            st.number_input("Loan interest rate (%)", min_value=0.0, step=0.25, key="loan_rate")
            st.number_input("Loan repayment period (years)", min_value=1, step=1, key="loan_years")
        st.checkbox("The project received a grant", key="has_grant")
        if st.session_state["has_grant"]:
            st.number_input("Grant amount ($)", min_value=0.0, step=5000.0, format="%.0f", key="grant")
        st.number_input("Investor dividend rate (%)", min_value=0.0, step=0.25, key="div_rate",
                        help="Paid each year on the investor capital still outstanding.")
        st.number_input("Dividends start in year #", min_value=1, step=1, key="div_start")
        st.number_input("Investor capital returned (% per year)", min_value=0.0, step=1.0,
                        key="ret_pct", help="Share of the original investment repaid each year.")
        st.number_input("Capital return starts in year #", min_value=1, step=1, key="ret_start")

    with st.expander("Operating costs"):
        st.caption("Each is a % of the total capital cost per year.")
        st.number_input("Maintenance (%)", min_value=0.0, step=0.1, key="maint")
        st.number_input("Administration (%)", min_value=0.0, step=0.1, key="admin")
        st.number_input("Insurance (%)", min_value=0.0, step=0.1, key="ins",
                        help="Currently 0 in the model, which is unusual for a 25-year asset.")
        st.number_input("Panel removal (%)", min_value=0.0, step=0.1, key="removal")
        st.number_input("Escalation / inflation (%/yr)", min_value=0.0, step=0.25, key="infl",
                        help="Applied to operating costs.")

    with st.expander("Timeline and NPV"):
        st.number_input("First year of the project", min_value=2000, step=1, key="start_year")
        st.number_input("Last year of the analysis", min_value=2000, step=1, key="end_year")
        st.number_input("Months operating in year 1", min_value=1, max_value=12, step=1,
                        key="months_y1", help="A project starting mid-year earns part of a year.")
        st.number_input("Discount rate for NPV (%)", min_value=0.0, step=0.25, key="disc",
                        help="Turns future dollars into today's dollars. Not in the Excel "
                             "projection; 2% is our assumption. Confirm with BVGEC.")

params = {k: st.session_state[k] for k in DEFAULTS}
if params["end_year"] < params["start_year"]:
    st.error("The last year of the analysis must be after the first year.")
    st.stop()
res = calculate(params)
t = res["table"]
chart = t.assign(Year=t["Year"].astype(str)).set_index("Year")   # years without commas

# ---------------------------------------------------------------- results
st.title("BVGEC Solar Project Calculator")
st.caption("Change any assumption on the left to see how it changes the project's value. "
           "Like a mortgage calculator, but for a community solar project.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Net present value (NPV)", money(res["npv"]),
          help="Today's value of all future profit, minus the capital cost. Above $0 means "
               "the project creates value.")
c2.metric("Project IRR", f"{res['irr']:.2%}" if res["irr"] is not None else "n/a",
          help="The yearly return the project earns on its capital cost.")
c3.metric("Payback", f"Year {res['payback']}" if res["payback"] else "Not within the analysis",
          help="The year cumulative profit first covers the capital cost.")
c4.metric("Total profit, all years", money(res["total_profit"]),
          help="Revenue minus operating costs, loan payments, dividends and capital returned.")

d1, d2, d3, d4 = st.columns(4)
d1.metric("Capital cost total", money(res["capex"]))
d2.metric("Annual loan payment", money(res["loan_payment"]))
d3.metric("Lifetime production", f"{res['lifetime_kwh']:,.0f} kWh")
d4.metric("Investor IRR", f"{res['investor_irr']:.2%}" if res["investor_irr"] is not None else "n/a",
          help="Return to members from dividends and capital returned.")

if res["npv"] < 0:
    st.error(md(f"NPV is negative ({money(res['npv'])}): with these assumptions the project "
                "costs more than it is worth today."))
elif res["first_short_year"]:
    st.warning(md(f"NPV is positive, but retained earnings go below $0 in {res['first_short_year']} "
                  f"(lowest point {money(res['min_cum_re'])}). From then on, members can't be paid "
                  "from the project's own earnings."))
else:
    st.success(md("NPV is positive and retained earnings stay above $0 every year, so members "
                  "can be paid from the project's own earnings."))

tab1, tab2, tab3, tab4 = st.tabs(["Over time", "Year-by-year table", "What moves NPV",
                                  "How it's calculated"])


def apply_changes(ch):
    """Copy an answer's changes into the sidebar inputs."""
    for k, v in ch.items():
        st.session_state[k] = v


def ask(q):
    st.session_state["pending_q"] = q

with tab1:
    st.subheader("Cumulative retained earnings")
    st.caption(md("Below $0 means the project can't pay members from its own earnings that year."))
    st.line_chart(chart[["Cumulative retained earnings"]], color=["#2a78d6"])
    st.subheader("Revenue and costs each year")
    st.bar_chart(chart[["Total revenue", "Total opex"]], stack=False,
                 color=["#2a78d6", "#eb6834"])

with tab2:
    st.caption("Like a mortgage amortization schedule: one row per year.")
    money_cols = [c for c in t.columns if c not in
                  ("Year", "Year #", "Energy production (kWh)", "Carbon offset (tCO2e)")]
    fmt = {c: "${:,.0f}" for c in money_cols}
    fmt.update({"Energy production (kWh)": "{:,.0f}", "Carbon offset (tCO2e)": "{:,.1f}",
                "Revenue per kWh": "${:,.4f}"})
    st.dataframe(t.style.format(fmt), hide_index=True, width="stretch")
    st.download_button("Download as CSV", t.to_csv(index=False).encode(),
                       "bvgec_projection.csv", "text/csv")

with tab3:
    st.subheader("Which assumptions move NPV the most")
    st.caption("Each assumption moved 10% down and 10% up, with everything else as set on the left.")
    sw = tornado_table(params)
    st.bar_chart(sw.set_index("Assumption")[["Swing"]], horizontal=True, color=["#2a78d6"])
    st.dataframe(sw.style.format({c: "${:,.0f}" for c in ("NPV at −10%", "NPV at +10%", "Swing")}),
                 hide_index=True, width="stretch")

    be = breakeven_output(params)
    if be:
        st.info(md(f"**Break-even output:** NPV reaches $0 at **{be:,.0f} kWh/yr**, "
                f"{1 - be / params['out']:.0%} below the {params['out']:,.0f} kWh assumption."))

with tab4:
    st.markdown("""
**Inputs → outputs, following BVGEC's Excel projection**

- **Capital cost total** = capital from investors + loan + grant
- **Annual loan payment** = loan × r(1+r)ⁿ ÷ ((1+r)ⁿ − 1)
- **Energy production:** year 1 = initial output × months/12; year 2 = initial output;
  year 3 onward = last year × (1 − degradation); zero after the panel life
- **Carbon offset (tCO2e)** = production × eCarbon ÷ 1,000
- **Carbon credit rate:** year 1 = initial rate; then + the yearly increase
- **Operating costs** = capital cost total × each % × portion of year
- **Revenue** = production × energy rate + carbon credits + Clean Tech ITC (year 2 only)
- **Profit / loss** = revenue − operating costs
- **Investor dividend** = investor capital still outstanding × dividend rate
- **Retained earnings** = profit − loan interest − loan principal − dividend − capital repaid
- **NPV** = −capital cost total + each year's profit ÷ (1 + discount rate) to the power of the year number
- **Payback** = first year cumulative profit covers the capital cost

**Assumptions to confirm with BVGEC**
- The **discount rate** is not in the Excel projection; 2% is our placeholder.
- The spreadsheet lists an **escalation/inflation** input but doesn't show where it applies.
  The calculator applies it to operating costs.
- The **Clean Tech ITC** is off by default; turn it on only if the project qualifies.
- The **energy rate increase** is optional and not part of the Excel projection.
""")


def agent_panel():
    """The chat: question box, answers, Apply buttons and follow-ups."""
    st.subheader("Ask the model")
    st.caption("Ask a what-if, ask why a result is what it is, or ask for suggestions. Answers use the same "
               "calculation as the rest of the app and start from the settings on the left.")
    chat = st.session_state.setdefault("chat", [])
    typed = st.chat_input("For example: what if output drops 10% and the energy rate falls to 0.12?",
                          key="agent_input")
    asked = typed or st.session_state.pop("pending_q", None)
    if asked:
        chat.append({"role": "user", "text": asked})
        chat.append({"role": "assistant", "answer": agent_answer(asked, params, st.session_state)})
    if not chat:
        st.markdown("Pick a question to start, or type your own below.")
        for j, ex in enumerate(EXAMPLES):
            st.button(ex, key=f"ex_{j}", on_click=ask, args=(ex,))
    last = len(chat) - 1
    for i, m in enumerate(chat):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(md(m["text"]))
                continue
            a = m["answer"]
            st.markdown(md(a["text"]))
            if a["changes"]:
                st.button("Apply these changes to the calculator", key=f"apply_{i}",
                          on_click=apply_changes, args=(a["changes"],))
            for j, (label, ch) in enumerate(a["options"]):
                st.button(label, key=f"opt_{i}_{j}", on_click=apply_changes, args=(ch,))
            with st.expander("How I got this"):
                for step in a["steps"]:
                    st.markdown(md("- " + step))
                for title, tbl in a["tables"]:
                    st.caption(title)
                    st.dataframe(tbl, hide_index=True, width="stretch")
            if i == last:
                for j, f in enumerate(a["followups"]):
                    st.button(f, key=f"fu_{i}_{j}", on_click=ask, args=(f,))
    if chat:
        st.button("Clear conversation", on_click=lambda: st.session_state.update(chat=[]))


# Floating button in the bottom-right corner that opens the chat on top of any tab
st.markdown("""
<style>
.st-key-agent_float { position: fixed; bottom: 24px; right: 24px; z-index: 1000; width: auto; }
.st-key-agent_float button { border-radius: 999px; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
[data-testid="stPopoverBody"] { width: min(560px, 92vw); max-height: 75vh; overflow-y: auto; }
</style>
""", unsafe_allow_html=True)
with st.container(key="agent_float"):
    with st.popover("💬 Ask the model"):
        agent_panel()
