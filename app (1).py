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


# ---------------------------------------------------------------- page
st.set_page_config(page_title="BVGEC Solar Project Calculator", layout="wide")
defaults, defaults_source = load_defaults()

for k, v in defaults.items():
    st.session_state.setdefault(k, v)


def reset():
    for k, v in defaults.items():
        st.session_state[k] = v


money = lambda v: f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"
md = lambda text: text.replace("$", "\\$")   # stop Markdown reading $ as maths

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
    SWING = [("out", "Panel output"), ("rate", "Energy rate"), ("investor", "Capital from investors"),
             ("deg", "Panel degradation"), ("maint", "Maintenance"), ("admin", "Administration"),
             ("infl", "Escalation / inflation"), ("disc", "Discount rate")]
    rows = []
    for key, label in SWING:
        lo = calculate(dict(params, **{key: params[key] * 0.9}))["npv"]
        hi = calculate(dict(params, **{key: params[key] * 1.1}))["npv"]
        rows.append({"Assumption": label, "NPV at −10%": lo, "NPV at +10%": hi,
                     "Swing": abs(hi - lo)})
    sw = pd.DataFrame(rows).sort_values("Swing", ascending=False)
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
