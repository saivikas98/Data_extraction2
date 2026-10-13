import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

from io import BytesIO
from pypdf import PdfReader
from pdf2image import convert_from_bytes
from PIL import Image

import cv2
import pytesseract


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

def extract_pdf_text(pdf_file):

    reader = PdfReader(pdf_file)

    text = ""

    for page in reader.pages:
        try:
            page_text = page.extract_text()

            if page_text:
                text += page_text + "\n"

        except Exception:
            pass

    return text


# ==========================================================
# OCR TREND PAGE DETECTION
# ==========================================================

def extract_ocr_pages(pdf_bytes):

    images = convert_from_bytes(
        pdf_bytes,
        dpi=300
    )

    trend_keywords = [
        "weight",
        "pressure",
        "temperature",
        "load cell"
    ]

    ocr_rows = []

    for page_no, img in enumerate(images):

        try:

            text = pytesseract.image_to_string(img)

            if any(
                k in text.lower()
                for k in trend_keywords
            ):

                ocr_rows.append(
                    {
                        "Page": page_no + 1,
                        "OCR_Text": text[:1000]
                    }
                )

        except Exception:
            pass

    return pd.DataFrame(ocr_rows), images


# ==========================================================
# GRAPH DIGITIZATION
# ==========================================================

def detect_blue_curve(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower = np.array([90, 50, 50])
    upper = np.array([140, 255, 255])

    mask = cv2.inRange(
        hsv,
        lower,
        upper
    )

    return mask


def detect_red_curve(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower1 = np.array([0, 70, 70])
    upper1 = np.array([10, 255, 255])

    lower2 = np.array([170, 70, 70])
    upper2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(
        hsv,
        lower1,
        upper1
    )

    mask2 = cv2.inRange(
        hsv,
        lower2,
        upper2
    )

    return mask1 + mask2


def extract_curve(mask):

    ys, xs = np.where(mask > 0)

    if len(xs) == 0:
        return pd.DataFrame()

    curve = pd.DataFrame(
        {
            "x": xs,
            "y": ys
        }
    )

    curve = (
        curve
        .groupby("x")
        .mean()
        .reset_index()
    )

    return curve


def digitize_graph(
    pil_image,
    ymin,
    ymax
):

    image = cv2.cvtColor(
        np.array(pil_image),
        cv2.COLOR_RGB2BGR
    )

    mask = detect_blue_curve(image)

    curve = extract_curve(mask)

    if curve.empty:

        mask = detect_red_curve(image)

        curve = extract_curve(mask)

    if curve.empty:
        return pd.DataFrame()

    height = image.shape[0]

    curve["Value"] = (
        ymax
        - (curve["y"] / height)
        * (ymax - ymin)
    )

    return curve


# ==========================================================
# SIMPLE ALARM EXTRACTION
# ==========================================================

def extract_alarm_rows(text):

    alarms = []

    for line in text.splitlines():

        if "alarm" in line.lower():

            alarms.append(
                {
                    "Alarm_Record": line
                }
            )

    return pd.DataFrame(alarms)


# ==========================================================
# AUDIT TRAIL EXTRACTION
# ==========================================================

def extract_audit_rows(text):

    rows = []

    start = False

    for line in text.splitlines():

        if "audit" in line.lower():
            start = True

        if start:
            rows.append(
                {
                    "Audit_Record": line
                }
            )

    return pd.DataFrame(rows)


# ==========================================================
# SUMMARY
# ==========================================================

def create_summary(text):

    return pd.DataFrame(
        {
            "Metric": [
                "Characters",
                "Lines"
            ],
            "Value": [
                len(text),
                len(text.splitlines())
            ]
        }
    )


# ==========================================================
# EXCEL EXPORT
# ==========================================================

def create_excel(data_dict):

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        for sheet_name, df in data_dict.items():

            if not df.empty:

                df.to_excel(
                    writer,
                    sheet_name=sheet_name[:31],
                    index=False
                )

    output.seek(0)

    return output


# ==========================================================
# MAIN APP
# ==========================================================

st.title("Batch Report Analyzer")

uploaded_file = st.file_uploader(
    "Upload PDF",
    type=["pdf"]
)

if uploaded_file:

    pdf_bytes = uploaded_file.read()

    with st.spinner("Processing PDF..."):

        text = extract_pdf_text(
            BytesIO(pdf_bytes)
        )

        summary_df = create_summary(text)

        alarm_df = extract_alarm_rows(text)

        audit_df = extract_audit_rows(text)

        ocr_df, images = extract_ocr_pages(
            pdf_bytes
        )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Summary",
            "OCR",
            "Alarms",
            "Audit",
            "Graph Digitizer"
        ]
    )

    with tab1:
        st.dataframe(
            summary_df,
            use_container_width=True
        )

    with tab2:
        st.dataframe(
            ocr_df,
            use_container_width=True
        )

    with tab3:
        st.dataframe(
            alarm_df,
            use_container_width=True
        )

    with tab4:
        st.dataframe(
            audit_df,
            use_container_width=True
        )

    with tab5:

        if len(images) > 0:

            page = st.selectbox(
                "Select Page",
                range(len(images)),
                format_func=lambda x: f"Page {x+1}"
            )

            ymin = st.number_input(
                "Y-Axis Minimum",
                value=0.0
            )

            ymax = st.number_input(
                "Y-Axis Maximum",
                value=100.0
            )

            image = images[page]

            st.image(
                image,
                use_container_width=True
            )

            curve_df = digitize_graph(
                image,
                ymin,
                ymax
            )

            if not curve_df.empty:

                fig = px.line(
                    curve_df,
                    x="x",
                    y="Value",
                    title="Digitized Trend Curve"
                )

                fig.update_layout(
                    hovermode="x unified"
                )

                st.plotly_chart(
                    fig,
                    use_container_width=True
                )

                st.dataframe(
                    curve_df,
                    use_container_width=True
                )

                st.download_button(
                    "Download Curve CSV",
                    curve_df.to_csv(index=False),
                    file_name="digitized_curve.csv",
                    mime="text/csv"
                )

    excel_file = create_excel(
        {
            "Summary": summary_df,
            "OCR": ocr_df,
            "Alarms": alarm_df,
            "Audit": audit_df
        }
    )

    st.download_button(
        "Download Excel Report",
        excel_file,
        file_name="Batch_Report_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
