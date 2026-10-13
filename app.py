import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from pypdf import PdfReader
from pdf2image import convert_from_bytes
from io import BytesIO

import pytesseract
import cv2
from PIL import Image

# ======================================================
# PAGE CONFIG
# ======================================================

st.set_page_config(
    page_title="Batch Report Analyzer",
    layout="wide"
)

# ======================================================
# PDF TEXT EXTRACTION
# ======================================================

def extract_pdf_text(pdf_file):

    reader = PdfReader(pdf_file)

    text = ""

    for page in reader.pages:

        try:
            txt = page.extract_text()

            if txt:
                text += txt + "\n"

        except:
            pass

    return text


# ======================================================
# OCR PAGE EXTRACTION
# ======================================================

def extract_ocr_pages(pdf_bytes):

    images = convert_from_bytes(
        pdf_bytes,
        dpi=300
    )

    ocr_rows = []

    trend_keywords = [
        "weight",
        "pressure",
        "temperature",
        "load cell"
    ]

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
                        "OCR Text": text[:500]
                    }
                )

        except:
            pass

    return pd.DataFrame(ocr_rows), images


# ======================================================
# DIGITAL GRAPH EXTRACTION
# ======================================================

def detect_blue_curve(image):

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV
    )

    lower = np.array([90,50,50])
    upper = np.array([140,255,255])

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

    lower1 = np.array([0,70,70])
    upper1 = np.array([10,255,255])

    lower2 = np.array([170,70,70])
    upper2 = np.array([180,255,255])

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


def convert_to_values(
        curve,
        ymin,
        ymax,
        img_height
):

    curve["Value"] = (
        ymax -
        (curve["y"]/img_height)
        *
        (ymax-ymin)
    )

    return curve


def digitize_page(
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

    if len(curve) == 0:

        mask = detect_red_curve(image)

        curve = extract_curve(mask)

    if len(curve) == 0:
        return pd.DataFrame()

    curve = convert_to_values(
        curve,
        ymin,
        ymax,
        image.shape[0]
    )

    return curve


# ======================================================
# ALARM EXTRACTION
# ======================================================

def extract_alarm_rows(text):

    rows = []

    for line in text.splitlines():

        if ":" in line and "/" in line:

            if "alarm" in line.lower():

                rows.append(
                    {
                        "Alarm": line
                    }
                )

    return pd.DataFrame(rows)


# ======================================================
# AUDIT EXTRACTION
# ======================================================

def extract_audit_rows(text):

    rows = []

    start = False

    for line in text.splitlines():

        if "audit" in line.lower():
            start = True

        if start:
            rows.append(
                {
                    "Audit": line
                }
            )

    return pd.DataFrame(rows)


# ======================================================
# SUMMARY
# ======================================================

def create_summary(text):

    return pd.DataFrame(
        {
            "Metric": [
                "Total Characters",
                "Total Lines"
            ],
            "Value": [
                len(text),
                len(text.splitlines())
            ]
        }
    )


# ======================================================
# EXCEL EXPORT
# ======================================================

def create_excel(data):

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        for name, df in data.items():

            if len(df):

                df.to_excel(
                    writer,
                    sheet_name=name[:31],
                    index=False
                )

    output.seek(0)

    return output


# ======================================================
# UI
# ======================================================

st.title("PDF Batch Report Analyzer")

uploaded = st.file_uploader(
    "Upload Report",
    type=["pdf"]
)

if uploaded:

    pdf_bytes = uploaded.read()

    with st.spinner(
        "Processing PDF..."
    ):

        text = extract_pdf_text(
            BytesIO(pdf_bytes)
        )

        summary_df = create_summary(text)

        alarm_df = extract_alarm_rows(text)

        audit_df = extract_audit_rows(text)

        ocr_df, images = extract_ocr_pages(
            pdf_bytes
        )

    tabs = st.tabs(
        [
            "Summary",
            "OCR",
            "Alarms",
            "Audit",
            "Digitizer"
        ]
    )

    with tabsst.dataframe(
            summary_df,
            use_container_width=True
        )

    with tabsst.dataframe(
            ocr_df,
            use_container_width=True
        )

    with tabsst.dataframe(
            alarm_df,
            use_container_width=True
        )

    with tabsst.dataframe(
            audit_df,
            use_container_width=True
        )

    with tabs[4]:

        if len(images):

            page = st.selectbox(
                "Graph Page",
                range(len(images)),
                format_func=lambda x:
                f"Page {x+1}"
            )

            ymin = st.number_input(
                "Y Min",
                value=0.0
            )

            ymax = st.number_input(
                "Y Max",
                value=100.0
            )

            img = images[page]

            st.image(
                img,
                use_container_width=True
            )

            curve = digitize_page(
                img,
                ymin,
                ymax
            )

            if len(curve):

                fig = px.line(
                    curve,
                    x="x",
                    y="Value",
                    title="Digitized Trend"
                )

                fig.update_layout(
                    hovermode="x unified"
                )

                st.plotly_chart(
                    fig,
                    use_container_width=True
                )

                st.download_button(
                    "Download CSV",
                    curve.to_csv(
                        index=False
                    ),
                    "digitized_curve.csv",
                    "text/csv"
                )

    excel = create_excel(
        {
            "Summary":
                summary_df,

            "OCR":
                ocr_df,

            "Alarms":
                alarm_df,

            "Audit":
                audit_df
        }
    )

    st.download_button(
        "Download Excel",
        excel,
        "Batch_Report_Analysis.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
