import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import fitz
import re
from io import BytesIO

# =====================================================
# PAGE CONFIG
# =====================================================

st.set_page_config(
    page_title="EG26058 Batch Report Analyzer",
    layout="wide"
)

# =====================================================
# PDF TEXT EXTRACTION
# =====================================================

@st.cache_data
def extract_pdf_text(pdf_bytes):

    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    text = ""

    for page in doc:
        try:
            text += page.get_text("text")
            text += "\n"
        except:
            pass

    return text


# =====================================================
# BATCH SUMMARY
# =====================================================

def extract_batch_summary(text):

    summary = {}

    try:
        m = re.search(
            r'Batch No\.\s*:\s*(.*?)\s*Batch Start Time',
            text,
            re.DOTALL
        )

        summary["Batch Number"] = (
            m.group(1).strip()
            if m else ""
        )
    except:
        summary["Batch Number"] = ""

    try:
        m = re.search(
            r'Batch Start Time\s*:\s*(.*?)\s*Batch End Time',
            text,
            re.DOTALL
        )

        summary["Batch Start"] = (
            m.group(1).strip()
            if m else ""
        )
    except:
        summary["Batch Start"] = ""

    try:
        m = re.search(
            r'Batch End Time\s*:\s*(.*?)\s*Batch Running Time',
            text,
            re.DOTALL
        )

        summary["Batch End"] = (
            m.group(1).strip()
            if m else ""
        )
    except:
        summary["Batch End"] = ""

    try:
        m = re.search(
            r'Batch Running Time.*?:\s*(.*?)\s*Batch Size',
            text,
            re.DOTALL
        )

        summary["Runtime"] = (
            m.group(1).strip()
            if m else ""
        )
    except:
        summary["Runtime"] = ""

    return pd.DataFrame([summary])


# =====================================================
# PRODUCTION COUNTERS
# =====================================================

def extract_production_counters(text):

    rows = []

    for line in text.splitlines():

        nums = re.findall(r'\d+', line)

        if (
            len(nums) >= 3 and
            "counter" in line.lower()
        ):

            rows.append(
                {
                    "Raw Data": line
                }
            )

    return pd.DataFrame(rows)


# =====================================================
# FILL WEIGHT TABLE
# =====================================================

def extract_fill_weights(text):

    rows = []

    pattern = re.compile(
        r'(\d{2}/\d{2}/\d{4})\s+'
        r'(\d{2}:\d{2}:\d{2})\s+'
        r'(-?\d+\.?\d*)\s+'
        r'(-?\d+\.?\d*)\s+'
        r'(-?\d+\.?\d*)\s+'
        r'([01])\s+'
        r'([01\-])\s+'
        r'([01\-])\s+'
        r'([01\-])\s+'
        r'([1-4])'
    )

    for m in pattern.finditer(text):

        rows.append(
            {
                "Timestamp":
                    f"{m.group(1)} {m.group(2)}",

                "Nett Weight":
                    float(m.group(3)),

                "Gross Weight":
                    float(m.group(4)),

                "Tare Weight":
                    float(m.group(5)),

                "Weight Passed":
                    int(m.group(6)),

                "Air Bubble":
                    m.group(7),

                "Needle Bonding":
                    m.group(8),

                "Assembly":
                    m.group(9),

                "Position":
                    int(m.group(10))
            }
        )

    return pd.DataFrame(rows)


# =====================================================
# SAFETY ALARMS
# =====================================================

def extract_safety_alarms(text):

    rows = []

    section = text

    pattern = re.compile(
        r'(\d{2}/\d{2}/\d{4})\s+'
        r'(\d{2}:\d{2}:\d{2})\s+'
        r'([\+\-\*])\s+'
        r'(.*?)\s+'
        r'(\d+)$',
        re.MULTILINE
    )

    for m in pattern.finditer(section):

        rows.append(
            {
                "Timestamp":
                    f"{m.group(1)} {m.group(2)}",

                "State":
                    m.group(3),

                "Message":
                    m.group(4),

                "Code":
                    m.group(5)
            }
        )

    return pd.DataFrame(rows)


# =====================================================
# GMP ALARMS
# =====================================================

def extract_gmp_alarms(text):

    rows = []

    for line in text.splitlines():

        if "gmp" in line.lower():

            rows.append(
                {
                    "Alarm": line
                }
            )

    return pd.DataFrame(rows)


# =====================================================
# EQUIPMENT ALARMS
# =====================================================

def extract_equipment_alarms(text):

    rows = []

    for line in text.splitlines():

        if "equipment" in line.lower():

            rows.append(
                {
                    "Alarm": line
                }
            )

    return pd.DataFrame(rows)


# =====================================================
# AUDIT TRAIL
# =====================================================

def extract_audit_trail(text):

    rows = []

    collect = False

    for line in text.splitlines():

        if "audit trail" in line.lower():
            collect = True

        if collect:

            if len(line.strip()):

                rows.append(
                    {
                        "Audit Record":
                            line
                    }
                )

    return pd.DataFrame(rows)


# =====================================================
# EXCEL EXPORT
# =====================================================

def build_excel(dfs):

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        for sheet, df in dfs.items():

            if not df.empty:

                df.to_excel(
                    writer,
                    sheet_name=sheet[:31],
                    index=False
                )

    output.seek(0)

    return output


# =====================================================
# UI
# =====================================================

st.title("EG26058 Batch Report Analyzer")

uploaded_file = st.file_uploader(
    "Upload PDF",
    type=["pdf"]
)

if uploaded_file:

    text = extract_pdf_text(
        uploaded_file.read()
    )

    summary_df = extract_batch_summary(text)

    production_df = extract_production_counters(text)

    fill_df = extract_fill_weights(text)

    safety_df = extract_safety_alarms(text)

    gmp_df = extract_gmp_alarms(text)

    equipment_df = extract_equipment_alarms(text)

    audit_df = extract_audit_trail(text)

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "Summary",
        "Production",
        "Fill Weight",
        "Safety",
        "GMP",
        "Equipment",
        "Audit"
    ])

    with tab1:
        st.dataframe(summary_df, use_container_width=True)

    with tab2:
        st.dataframe(production_df, use_container_width=True)

    with tab3:

        st.dataframe(
            fill_df,
            use_container_width=True
        )

        if not fill_df.empty:

            st.subheader("Weight Trend")

            fig = px.line(
                fill_df,
                x="Timestamp",
                y="Nett Weight",
                color="Position"
            )

            st.plotly_chart(
                fig,
                use_container_width=True
            )

            st.subheader("Weight Distribution")

            fig2 = px.histogram(
                fill_df,
                x="Nett Weight",
                nbins=50
            )

            st.plotly_chart(
                fig2,
                use_container_width=True
            )

            pass_rate = (
                fill_df["Weight Passed"]
                .mean()
                * 100
            )

            st.metric(
                "Pass Rate %",
                f"{pass_rate:.2f}"
            )

    with tab4:
        st.dataframe(
            safety_df,
            use_container_width=True
        )

    with tab5:
        st.dataframe(
            gmp_df,
            use_container_width=True
        )

    with tab6:
        st.dataframe(
            equipment_df,
            use_container_width=True
        )

    with tab7:
        st.dataframe(
            audit_df,
            use_container_width=True
        )

    excel_file = build_excel(
        {
            "Summary": summary_df,
            "Production": production_df,
            "FillWeight": fill_df,
            "Safety": safety_df,
            "GMP": gmp_df,
            "Equipment": equipment_df,
            "AuditTrail": audit_df
        }
    )

    st.download_button(
        "Download Excel",
        data=excel_file,
        file_name="Batch_Report_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
