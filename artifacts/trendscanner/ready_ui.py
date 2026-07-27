import streamlit as st

from ready_engine import build_ready_report


def render_ready_section(all_results: dict) -> None:
    """
    Draw READY candidates.
    Read-only layer above Legacy Signal Grid.
    """

    ready_report = build_ready_report(all_results)
    candidates = ready_report.get("candidates", [])

    st.markdown("---")
    st.subheader("⭐ Top Ready Setups")

    if not candidates:
        st.info(
            "No READY setups right now.\n\n"
            "The scanner has not found any high-quality "
            "candidate waiting for ENTRY NOW."
        )
        return

    st.caption(
        f"{len(candidates)} setup(s) waiting for ENTRY confirmation."
    )

    for setup in candidates[:6]:

        direction = setup["direction"]
        icon = "🟢" if direction == "LONG" else "🔴"

        st.markdown(
            f"""
### {icon} {setup["symbol"]}

**{direction}**

Timeframe: **{setup["timeframe"]}**

Confidence:
**{setup["confidence"]:.1f}%**

Decision score:
**{setup["decision_score"]:.1f}**
"""
        )