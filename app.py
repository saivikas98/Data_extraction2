import streamlit as st
import pandas as pd
import pymupdf as fitz
import re

from io import BytesIO

# ==========================================================
# PAGE CONFIG
# ==========================================================

st.set_page_config(
    page_title="Batch Report Analyzer",
    layout="wide"
)

# ==========================================================
# PDF TEXT EXTRACTION
# ==========================================================

@st.cache_data
def extract_pdf_text(pdf_bytes):

    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    pages = []
    full_text = ""

    for page_num, page in enumerate(doc):

        text = page.get_text()

        pages.append(
            {
                "Page": page_num + 1,
                "Text": text
            }
        )

        full_text += f"\n\n===== PAGE {page_num+1} =====\n\n"
        full_text += text

    return full_text, pages


# ==========================================================
# SUMMARY
# ==========================================================

def create_summary(text, pages):

    return pd.DataFrame(
        {
            "Metric": [
                "Pages",
                "Characters",
                "Lines"
            ],
            "Value": [
                len(pages),
                len(text),
                len(text.splitlines())
            ]
        }
    )


# ==========================================================
# FIND SECTIONS
# ==========================================================

def detect_sections(pages):

    section_rows = []

    keywords = {
        "Fill Weight":
            [
                "net weight",
                "gross weight",
                "tare weight"
            ],

        "Safety Alarm":
            [
                "safety alarm"
            ],

        "GMP Alarm":
            [
                "gmp alarm"
            ],

        "Equipment Alarm":
            [
                "equipment alarm"
            ],

        "Audit Trail":
            [
                "audit trail"
            ]
    }

    for page in pages:

        page_no = page["Page"]

        txt = page["Text"].lower()

        for section, words in keywords.items():

            if any(
                word in txt
                for word in words
            ):

                section_rows.append(
                    {
                        "Section": section,
                        "Page": page_no
                    }
                )

    return pd.DataFrame(section_rows)


# ==========================================================
# FILL WEIGHT PAGE EXTRACTOR
# ==========================================================

def extract_fill_weight_pages(pages):

    rows = []

    keywords = [
        "net weight",
        "gross weight",
        "tare"
    ]

    for page in pages:

        txt = page["Text"].lower()

        if any(
            k in txt
            for k in keywords
        ):

            rows.append(
                {
                    "Page": page["Page"],
                    "Content": page["Text"]
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# SAFETY ALARMS
# ==========================================================

def extract_safety_alarms(pages):

    rows = []

    for page in pages:

        txt = page["Text"]

        if "safety" in txt.lower():

            rows.append(
                {
                    "Page": page["Page"],
                    "Alarm_Data": txt
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# GMP ALARMS
# ==========================================================

def extract_gmp_alarms(pages):

    rows = []

    for page in pages:

        txt = page["Text"]

        if "gmp" in txt.lower():

            rows.append(
                {
                    "Page": page["Page"],
                    "Alarm_Data": txt
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# EQUIPMENT ALARMS
# ==========================================================

def extract_equipment_alarms(pages):

    rows = []

    for page in pages:

        txt = page["Text"]

        if "equipment" in txt.lower():

            rows.append(
                {
                    "Page": page["Page"],
                    "Alarm_Data": txt
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# AUDIT TRAIL
# ==========================================================

def extract_audit_trail(pages):

    rows = []

    collect = False

    for page in pages:

        text = page["Text"]

        if "audit trail" in text.lower():
            collect = True

        if collect:

            rows.append(
                {
                    "Page": page["Page"],
                    "Audit_Content": text
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# EXCEL EXPORT
# ==========================================================

def to_excel(dataframes):

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        for name, df in dataframes.items():

            if not df.empty:

                df.to_excel(
                    writer,
                    sheet_name=name[:31],
                    index=False
                )

    output.seek(0)

    return output


# ==========================================================
# MAIN APP
# ==========================================================

st.title("PDF Batch Record Analyzer")

uploaded_file = st.file_uploader(
    "Upload PDF",
    type=["pdf"]
)

if uploaded_file:

    pdf_bytes = uploaded_file.read()

    with st.spinner("Reading PDF..."):

        full_text, pages = extract_pdf_text(
            pdf_bytes
        )

        summary_df = create_summary(
            full_text,
            pages
        )

        section_df = detect_sections(
            pages
        )

        fillweight_df = extract_fill_weight_pages(
            pages
        )

        safety_df = extract_safety_alarms(
            pages
        )

        gmp_df = extract_gmp_alarms(
            pages
        )

        equipment_df = extract_equipment_alarms(
            pages
        )

        audit_df = extract_audit_trail(
            pages
        )

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        [
            "Summary",
            "Sections",
            "Fill Weight",
            "Safety",
            "GMP",
            "Equipment",
            "Audit Trail"
        ]
    )

    with tab1:
        st.dataframe(
            summary_df,
            use_container_width=True
        )

    with tab2:
        st.dataframe(
            section_df,
            use_container_width=True
        )

    with tab3:
        st.dataframe(
            fillweight_df,
            use_container_width=True
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

    excel_file = to_excel(
        {
            "Summary": summary_df,
            "Sections": section_df,
            "FillWeight": fillweight_df,
            "Safety": safety_df,
            "GMP": gmp_df,
            "Equipment": equipment_df,
            "AuditTrail": audit_df,
        }
    )

    st.download_button(
        "Download Excel",
        data=excel_file,
        file_name="Batch_Report_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.subheader("Raw PDF Text")

    st.text_area(
        "Extracted Content",
        full_text[:50000],
        height=400
    )
