import re
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    import pymupdf as fitz
except ImportError:
    import fitz


# =====================================================
# PAGE CONFIGURATION
# =====================================================
st.set_page_config(
    page_title="Batch Report Analyzer",
    layout="wide",
)


# =====================================================
# HELPERS
# =====================================================
def normalize_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def empty_dataframe(columns):
    return pd.DataFrame(columns=columns)


def find_first(text, patterns, default=""):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_space(match.group(1))
    return default


def find_section_bounds(pages, include_terms, stop_terms=None):
    """Return pages belonging to a report section based on page headers."""
    selected = []
    collecting = False
    stop_terms = stop_terms or []

    for page in pages:
        page_text = page["Text"]
        lower = page_text.lower()

        if any(term.lower() in lower for term in include_terms):
            collecting = True

        if collecting and selected and any(term.lower() in lower for term in stop_terms):
            break

        if collecting:
            selected.append(page)

    return selected


# =====================================================
# PDF TEXT EXTRACTION
# =====================================================
@st.cache_data(show_spinner=False)
def extract_pdf_pages(pdf_bytes):
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []

    for page_number, page in enumerate(document, start=1):
        page_text = page.get_text("text", sort=True) or ""
        pages.append({"Page": page_number, "Text": page_text})

    document.close()
    full_text = "\n".join(page["Text"] for page in pages)
    return full_text, pages


# =====================================================
# BATCH SUMMARY
# =====================================================
def extract_batch_summary(text):
    summary = {
        "Batch Number": find_first(
            text,
            [
                r"Batch\s*No\.?\s*:\s*(.*?)\s*Batch\s*Start\s*Time",
                r"Batch\s*Number\s*:\s*(.*?)\s*(?:\n|$)",
            ],
        ),
        "Batch Start": find_first(
            text,
            [r"Batch\s*Start\s*Time\s*:\s*(.*?)\s*Batch\s*End\s*Time"],
        ),
        "Batch End": find_first(
            text,
            [r"Batch\s*End\s*Time\s*:\s*(.*?)\s*Batch\s*Running\s*Time"],
        ),
        "Runtime": find_first(
            text,
            [r"Batch\s*Running\s*Time(?:\s*\([^)]*\))?\s*:\s*(.*?)\s*Batch\s*Size"],
        ),
        "Batch Size": find_first(
            text,
            [r"Batch\s*Size\s*:\s*(.*?)\s*(?:Product\s*Name|Recipe|\n)"],
        ),
        "Product Name": find_first(
            text,
            [r"Product\s*Name\s*:\s*(.*?)\s*(?:Batch\s*No|Recipe|\n)"],
        ),
        "Recipe Name": find_first(
            text,
            [
                r"Filling\s*Skid\s*Recipe\s*Name\s*:?\s*(.*?)\s*(?:\n|$)",
                r"Recipe\s*Name\s*:\s*(.*?)\s*(?:\n|$)",
            ],
        ),
    }
    return pd.DataFrame([summary])


# =====================================================
# PRODUCTION COUNTERS
# =====================================================
def extract_production_counters(text):
    counter_names = [
        "Infeed",
        "Outfeed",
        "Reject",
        "Accepted",
        "Produced",
        "Total",
    ]
    rows = []

    section_match = re.search(
        r"Production\s*Counters?\s*:?(.*?)(?=\n\s*(?:Fill\s*Weight|Check\s*Weigh|Alarms?|Audit\s*Trail)|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    section = section_match.group(1) if section_match else text

    for name in counter_names:
        match = re.search(
            rf"\b{re.escape(name)}\b\s*[:=-]?\s*(\d+)",
            section,
            flags=re.IGNORECASE,
        )
        if match:
            rows.append({"Counter": name, "Value": int(match.group(1))})

    if not rows:
        lines = [normalize_space(line) for line in section.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if "production counter" in line.lower():
                for candidate in lines[index + 1 : index + 15]:
                    match = re.match(r"(.+?)\s*[:=-]?\s*(\d+)$", candidate)
                    if match:
                        rows.append(
                            {
                                "Counter": normalize_space(match.group(1)),
                                "Value": int(match.group(2)),
                            }
                        )
                break

    return pd.DataFrame(rows, columns=["Counter", "Value"])


# =====================================================
# FILL WEIGHT TABLE
# =====================================================
def extract_fill_weights(text):
    columns = [
        "Timestamp",
        "Nett Weight",
        "Gross Weight",
        "Tare Weight",
        "Weight Passed",
        "Air Bubble",
        "Needle Bonding",
        "Assembly",
        "Position",
    ]

    # The final flags can appear as 0, 1 or hyphen. Position is expected as 1-4.
    pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+"
        r"(-?\d+(?:\.\d+)?)\s+"
        r"(-?\d+(?:\.\d+)?)\s+"
        r"(-?\d+(?:\.\d+)?)\s+"
        r"([01])\s+"
        r"([01-])\s+"
        r"([01-])\s+"
        r"([01-])\s+"
        r"([1-4])(?:\s|$)",
        flags=re.MULTILINE,
    )

    rows = []
    for match in pattern.finditer(text):
        rows.append(
            {
                "Timestamp": f"{match.group(1)} {match.group(2)}",
                "Nett Weight": float(match.group(3)),
                "Gross Weight": float(match.group(4)),
                "Tare Weight": float(match.group(5)),
                "Weight Passed": int(match.group(6)),
                "Air Bubble": match.group(7),
                "Needle Bonding": match.group(8),
                "Assembly": match.group(9),
                "Position": int(match.group(10)),
            }
        )

    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["Timestamp"] = pd.to_datetime(
            frame["Timestamp"], dayfirst=True, errors="coerce"
        )
        frame = frame.drop_duplicates().sort_values("Timestamp").reset_index(drop=True)
    return frame


# =====================================================
# ALARM EXTRACTION
# =====================================================
def parse_alarm_records(section_text, alarm_type):
    columns = ["Timestamp", "State", "Message", "Code", "Alarm Type"]
    rows = []

    # Supports a message wrapping onto multiple lines until the numerical code.
    record_pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+"
        r"([+*-])\s+"
        r"(.+?)\s+"
        r"(\d{2,})\s*(?=(?:\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+[+*-])|$)",
        flags=re.DOTALL,
    )

    for match in record_pattern.finditer(section_text):
        message = normalize_space(match.group(4))
        message = re.sub(
            r"(?:Page\s+\d+\s+of\s+\d+|Alarms?\s+(?:Safety|GMP|Equipment))",
            " ",
            message,
            flags=re.IGNORECASE,
        )
        message = normalize_space(message)

        rows.append(
            {
                "Timestamp": f"{match.group(1)} {match.group(2)}",
                "State": match.group(3),
                "Message": message,
                "Code": match.group(5),
                "Alarm Type": alarm_type,
            }
        )

    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["Timestamp"] = pd.to_datetime(
            frame["Timestamp"], dayfirst=True, errors="coerce"
        )
        frame = frame.drop_duplicates().sort_values("Timestamp").reset_index(drop=True)
    return frame


def extract_alarm_section(pages, section_terms, stop_terms, alarm_type):
    selected_pages = find_section_bounds(pages, section_terms, stop_terms)
    section_text = "\n".join(page["Text"] for page in selected_pages)

    if not section_text:
        return empty_dataframe(
            ["Timestamp", "State", "Message", "Code", "Alarm Type"]
        )

    return parse_alarm_records(section_text, alarm_type)


def extract_safety_alarms(pages):
    return extract_alarm_section(
        pages,
        ["alarms safety", "safety alarms", "alarm safety"],
        ["alarms gmp", "gmp alarms", "equipment alarms", "audit trail"],
        "Safety",
    )


def extract_gmp_alarms(pages):
    return extract_alarm_section(
        pages,
        ["alarms gmp", "gmp alarms", "alarm gmp"],
        ["equipment alarms", "alarms equipment", "audit trail"],
        "GMP",
    )


def extract_equipment_alarms(pages):
    return extract_alarm_section(
        pages,
        ["equipment alarms", "alarms equipment", "alarm equipment"],
        ["audit trail"],
        "Equipment",
    )


# =====================================================
# AUDIT TRAIL EXTRACTION
# =====================================================
def extract_audit_trail(pages):
    columns = ["Timestamp", "State", "User", "Action", "Audit Record"]
    selected_pages = find_section_bounds(pages, ["audit trail"], [])
    section_text = "\n".join(page["Text"] for page in selected_pages)

    if not section_text:
        return empty_dataframe(columns)

    # Each audit event starts with date/time. The remainder is retained as a full
    # action string because individual report versions can contain different fields.
    event_pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+"
        r"([+*-])?\s*"
        r"(.+?)"
        r"(?=(?:\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})|$)",
        flags=re.DOTALL,
    )

    rows = []
    for match in event_pattern.finditer(section_text):
        record = normalize_space(match.group(4))
        record = re.sub(
            r"(?:Page\s+\d+\s+of\s+\d+|Audit\s*Trail)",
            " ",
            record,
            flags=re.IGNORECASE,
        )
        record = normalize_space(record)

        if not record or len(record) < 2:
            continue

        # Common audit layouts place the username first. If it cannot be isolated,
        # the complete record is still preserved in Action and Audit Record.
        user = ""
        action = record
        user_match = re.match(
            r"(?:User\s*[:=-]\s*)?([A-Za-z][A-Za-z0-9_.@\\-]{1,40})\s+(.*)$",
            record,
        )
        if user_match:
            candidate_user = user_match.group(1)
            candidate_action = user_match.group(2)
            excluded_headers = {
                "date",
                "time",
                "state",
                "audit",
                "page",
                "event",
                "message",
                "description",
            }
            if candidate_user.lower() not in excluded_headers:
                user = candidate_user
                action = candidate_action

        rows.append(
            {
                "Timestamp": f"{match.group(1)} {match.group(2)}",
                "State": match.group(3) or "",
                "User": user,
                "Action": action,
                "Audit Record": record,
            }
        )

    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["Timestamp"] = pd.to_datetime(
            frame["Timestamp"], dayfirst=True, errors="coerce"
        )
        frame = frame.drop_duplicates().sort_values("Timestamp").reset_index(drop=True)
    return frame


# =====================================================
# SUMMARIES
# =====================================================
def create_alarm_summary(alarm_df):
    columns = ["Code", "Message", "Occurrences"]
    if alarm_df.empty:
        return empty_dataframe(columns)

    summary = (
        alarm_df.groupby(["Code", "Message"], dropna=False)
        .size()
        .reset_index(name="Occurrences")
        .sort_values(["Occurrences", "Message"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return summary


def create_audit_action_summary(audit_df):
    columns = ["Action", "Occurrences"]
    if audit_df.empty:
        return empty_dataframe(columns)

    summary = (
        audit_df.assign(Action=audit_df["Action"].fillna("").map(normalize_space))
        .query("Action != ''")
        .groupby("Action", dropna=False)
        .size()
        .reset_index(name="Occurrences")
        .sort_values(["Occurrences", "Action"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return summary


def create_audit_user_summary(audit_df):
    columns = ["User", "Occurrences"]
    if audit_df.empty or "User" not in audit_df.columns:
        return empty_dataframe(columns)

    user_data = audit_df[audit_df["User"].fillna("").str.strip().ne("")]
    if user_data.empty:
        return empty_dataframe(columns)

    return (
        user_data.groupby("User")
        .size()
        .reset_index(name="Occurrences")
        .sort_values("Occurrences", ascending=False)
        .reset_index(drop=True)
    )


# =====================================================
# PLOTLY FIGURES
# =====================================================
def apply_graph_style(fig, title, x_title, y_title):
    fig.update_layout(
        title={"text": title, "x": 0.5, "xanchor": "center"},
        xaxis_title=x_title,
        yaxis_title=y_title,
        template="plotly_white",
        font=dict(family="Times New Roman", size=12),
        title_font=dict(family="Times New Roman", size=24),
        legend_title_text="",
        hovermode="closest",
        margin=dict(l=60, r=30, t=80, b=60),
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#b8b8b8",
        gridwidth=1,
        title_font=dict(family="Times New Roman", size=12),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#b8b8b8",
        gridwidth=1,
        title_font=dict(family="Times New Roman", size=12),
    )
    return fig


def make_fill_scatter(fill_pass_df, batch_label, lower_limit, upper_limit):
    fig = px.scatter(
        fill_pass_df,
        x="Sample No",
        y="Nett Weight",
        color="Position",
        color_discrete_sequence=px.colors.qualitative.Safe,
        hover_data=[
            "Timestamp",
            "Gross Weight",
            "Tare Weight",
            "Position",
        ],
    )
    fig.update_traces(marker=dict(size=7, opacity=0.82))
    fig.add_hline(
        y=upper_limit,
        line_width=4,
        line_color="#9e9e9e",
        annotation_text=f"Upper limit: {upper_limit:g}",
        annotation_position="top left",
    )
    fig.add_hline(
        y=lower_limit,
        line_width=4,
        line_color="#f28e2b",
        annotation_text=f"Lower limit: {lower_limit:g}",
        annotation_position="bottom left",
    )
    return apply_graph_style(
        fig,
        f"{batch_label} - Fill weight data",
        "Sample Number",
        "Nett Weight",
    )


def make_fill_histogram(fill_pass_df, batch_label, bins):
    fig = px.histogram(
        fill_pass_df,
        x="Nett Weight",
        nbins=bins,
        color_discrete_sequence=["#5b9bd5"],
    )
    fig.update_traces(marker_line_color="white", marker_line_width=0.6)
    return apply_graph_style(
        fig,
        f"{batch_label} - Fill weight data",
        "Nett Weight",
        "Frequency",
    )


# =====================================================
# EXCEL EXPORT
# =====================================================
def build_excel(dataframes):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheets_written = 0
        for sheet_name, dataframe in dataframes.items():
            if dataframe is None or dataframe.empty:
                continue
            export_df = dataframe.copy()
            for column in export_df.select_dtypes(include=["datetimetz"]).columns:
                export_df[column] = export_df[column].dt.tz_localize(None)
            export_df.to_excel(
                writer,
                sheet_name=sheet_name[:31],
                index=False,
            )
            sheets_written += 1

        if sheets_written == 0:
            pd.DataFrame({"Information": ["No data was extracted."]}).to_excel(
                writer,
                sheet_name="Information",
                index=False,
            )

    output.seek(0)
    return output


# =====================================================
# STREAMLIT UI
# =====================================================
st.title("Batch Report Analyzer")
st.caption(
    "Structured extraction of fill weights, Safety/GMP/Equipment alarms, "
    "audit trail records, summaries and downloadable Excel tables."
)

uploaded_file = st.file_uploader("Upload batch report PDF", type=["pdf"])

if uploaded_file is None:
    st.info("Upload a PDF report to begin extraction.")
    st.stop()

pdf_bytes = uploaded_file.getvalue()

try:
    with st.spinner("Extracting and structuring the PDF report..."):
        full_text, pages = extract_pdf_pages(pdf_bytes)
        summary_df = extract_batch_summary(full_text)
        production_df = extract_production_counters(full_text)
        fill_df = extract_fill_weights(full_text)
        safety_df = extract_safety_alarms(pages)
        gmp_df = extract_gmp_alarms(pages)
        equipment_df = extract_equipment_alarms(pages)
        audit_df = extract_audit_trail(pages)
except Exception as exc:
    st.error(f"The PDF could not be processed: {exc}")
    st.stop()

fill_pass_df = fill_df[fill_df["Weight Passed"].eq(1)].copy()
if not fill_pass_df.empty:
    fill_pass_df = fill_pass_df.sort_values("Timestamp").reset_index(drop=True)
    fill_pass_df["Sample No"] = range(1, len(fill_pass_df) + 1)

safety_summary_df = create_alarm_summary(safety_df)
gmp_summary_df = create_alarm_summary(gmp_df)
equipment_summary_df = create_alarm_summary(equipment_df)
audit_action_summary_df = create_audit_action_summary(audit_df)
audit_user_summary_df = create_audit_user_summary(audit_df)

batch_number = ""
if not summary_df.empty:
    batch_number = normalize_space(summary_df.iloc[0].get("Batch Number", ""))
batch_label = batch_number or uploaded_file.name.rsplit(".", 1)[0]

# Sidebar graph controls
st.sidebar.header("Fill Weight Graph Settings")
lower_limit = st.sidebar.number_input("Lower fill-weight limit", value=526.0)
upper_limit = st.sidebar.number_input("Upper fill-weight limit", value=540.0)
histogram_bins = st.sidebar.slider("Histogram bins", min_value=5, max_value=60, value=20)

if lower_limit >= upper_limit:
    st.sidebar.error("Lower limit must be less than upper limit.")

(
    summary_tab,
    production_tab,
    fill_tab,
    safety_tab,
    gmp_tab,
    equipment_tab,
    audit_tab,
) = st.tabs(
    [
        "Summary",
        "Production",
        "Fill Weight",
        "Safety Alarms",
        "GMP Alarms",
        "Equipment Alarms",
        "Audit Trail",
    ]
)

with summary_tab:
    st.subheader("Batch Summary")
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    total_fill_records = len(fill_df)
    passed_fill_records = len(fill_pass_df)
    rejected_fill_records = total_fill_records - passed_fill_records
    pass_rate = (
        passed_fill_records / total_fill_records * 100 if total_fill_records else 0.0
    )

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Total Fill Records", f"{total_fill_records:,}")
    metric_2.metric("Fill Weight Passed", f"{passed_fill_records:,}")
    metric_3.metric("Fill Weight Not Passed", f"{rejected_fill_records:,}")
    metric_4.metric("Pass Rate", f"{pass_rate:.2f}%")

    event_1, event_2, event_3, event_4 = st.columns(4)
    event_1.metric("Safety Alarm Events", f"{len(safety_df):,}")
    event_2.metric("GMP Alarm Events", f"{len(gmp_df):,}")
    event_3.metric("Equipment Alarm Events", f"{len(equipment_df):,}")
    event_4.metric("Audit Records", f"{len(audit_df):,}")

with production_tab:
    st.subheader("Production Counters")
    if production_df.empty:
        st.warning("No structured production counters were detected.")
    else:
        st.dataframe(production_df, use_container_width=True, hide_index=True)

with fill_tab:
    st.subheader("Fill Weight Passed Records")

    if fill_df.empty:
        st.warning("No fill-weight records were detected in the PDF text.")
    else:
        fill_metric_1, fill_metric_2, fill_metric_3, fill_metric_4 = st.columns(4)
        fill_metric_1.metric("Passed Records", f"{len(fill_pass_df):,}")
        fill_metric_2.metric(
            "Mean Nett Weight",
            f"{fill_pass_df['Nett Weight'].mean():.2f}" if not fill_pass_df.empty else "N/A",
        )
        fill_metric_3.metric(
            "Standard Deviation",
            f"{fill_pass_df['Nett Weight'].std():.2f}" if len(fill_pass_df) > 1 else "N/A",
        )
        fill_metric_4.metric(
            "Range",
            (
                f"{fill_pass_df['Nett Weight'].min():.2f} to "
                f"{fill_pass_df['Nett Weight'].max():.2f}"
                if not fill_pass_df.empty
                else "N/A"
            ),
        )

        display_filter = st.radio(
            "Fill weight table",
            ["Passed only", "All records"],
            horizontal=True,
        )
        displayed_fill_df = fill_pass_df if display_filter == "Passed only" else fill_df
        st.dataframe(displayed_fill_df, use_container_width=True, hide_index=True)

        if not fill_pass_df.empty and lower_limit < upper_limit:
            scatter_figure = make_fill_scatter(
                fill_pass_df,
                batch_label,
                lower_limit,
                upper_limit,
            )
            st.plotly_chart(scatter_figure, use_container_width=True)

            histogram_figure = make_fill_histogram(
                fill_pass_df,
                batch_label,
                histogram_bins,
            )
            st.plotly_chart(histogram_figure, use_container_width=True)
        elif fill_pass_df.empty:
            st.warning("No records with Weight Passed = 1 were detected.")

with safety_tab:
    st.subheader("Safety Alarm Table")

    if safety_df.empty:
        st.warning("No structured Safety alarm records were detected.")
    else:
        safety_metric_1, safety_metric_2, safety_metric_3 = st.columns(3)
        safety_metric_1.metric("Total Alarm Events", f"{len(safety_df):,}")
        safety_metric_2.metric("Unique Alarm Types", f"{len(safety_summary_df):,}")
        safety_metric_3.metric(
            "Repeated Alarm Types",
            f"{int((safety_summary_df['Occurrences'] > 1).sum()):,}",
        )

        st.dataframe(safety_df, use_container_width=True, hide_index=True)

        st.subheader("Safety Alarm Frequency Summary")
        st.dataframe(safety_summary_df, use_container_width=True, hide_index=True)

        top_safety = safety_summary_df.head(20).sort_values("Occurrences")
        if not top_safety.empty:
            safety_chart = px.bar(
                top_safety,
                x="Occurrences",
                y="Message",
                orientation="h",
                color_discrete_sequence=["#d9534f"],
                hover_data=["Code"],
            )
            safety_chart = apply_graph_style(
                safety_chart,
                "Top Safety Alarms by Frequency",
                "Occurrences",
                "Alarm Message",
            )
            st.plotly_chart(safety_chart, use_container_width=True)

with gmp_tab:
    st.subheader("GMP Alarm Table")

    if gmp_df.empty:
        st.warning("No structured GMP alarm records were detected.")
    else:
        gmp_metric_1, gmp_metric_2, gmp_metric_3 = st.columns(3)
        gmp_metric_1.metric("Total Alarm Events", f"{len(gmp_df):,}")
        gmp_metric_2.metric("Unique Alarm Types", f"{len(gmp_summary_df):,}")
        gmp_metric_3.metric(
            "Repeated Alarm Types",
            f"{int((gmp_summary_df['Occurrences'] > 1).sum()):,}",
        )
        st.dataframe(gmp_df, use_container_width=True, hide_index=True)
        st.subheader("GMP Alarm Frequency Summary")
        st.dataframe(gmp_summary_df, use_container_width=True, hide_index=True)

with equipment_tab:
    st.subheader("Equipment Alarm Table")

    if equipment_df.empty:
        st.warning("No structured Equipment alarm records were detected.")
    else:
        equipment_metric_1, equipment_metric_2, equipment_metric_3 = st.columns(3)
        equipment_metric_1.metric("Total Alarm Events", f"{len(equipment_df):,}")
        equipment_metric_2.metric("Unique Alarm Types", f"{len(equipment_summary_df):,}")
        equipment_metric_3.metric(
            "Repeated Alarm Types",
            f"{int((equipment_summary_df['Occurrences'] > 1).sum()):,}",
        )
        st.dataframe(equipment_df, use_container_width=True, hide_index=True)
        st.subheader("Equipment Alarm Frequency Summary")
        st.dataframe(equipment_summary_df, use_container_width=True, hide_index=True)

with audit_tab:
    st.subheader("Audit Trail Table")

    if audit_df.empty:
        st.warning("No structured audit-trail records were detected.")
    else:
        audit_metric_1, audit_metric_2, audit_metric_3 = st.columns(3)
        audit_metric_1.metric("Total Audit Records", f"{len(audit_df):,}")
        audit_metric_2.metric("Unique Actions", f"{len(audit_action_summary_df):,}")
        audit_metric_3.metric(
            "Repeated Actions",
            f"{int((audit_action_summary_df['Occurrences'] > 1).sum()):,}",
        )

        st.dataframe(audit_df, use_container_width=True, hide_index=True)

        st.subheader("Audit Action Frequency Summary")
        st.dataframe(
            audit_action_summary_df,
            use_container_width=True,
            hide_index=True,
        )

        if not audit_user_summary_df.empty:
            st.subheader("Audit Records by User")
            st.dataframe(
                audit_user_summary_df,
                use_container_width=True,
                hide_index=True,
            )

        top_audit = audit_action_summary_df.head(20).sort_values("Occurrences")
        if not top_audit.empty:
            audit_chart = px.bar(
                top_audit,
                x="Occurrences",
                y="Action",
                orientation="h",
                color_discrete_sequence=["#5b9bd5"],
            )
            audit_chart = apply_graph_style(
                audit_chart,
                "Top Audit Actions by Frequency",
                "Occurrences",
                "Audit Action",
            )
            st.plotly_chart(audit_chart, use_container_width=True)

excel_file = build_excel(
    {
        "Batch Summary": summary_df,
        "Production Counters": production_df,
        "Fill Weight All": fill_df,
        "Fill Weight Passed": fill_pass_df,
        "Safety Alarms": safety_df,
        "Safety Summary": safety_summary_df,
        "GMP Alarms": gmp_df,
        "GMP Summary": gmp_summary_df,
        "Equipment Alarms": equipment_df,
        "Equipment Summary": equipment_summary_df,
        "Audit Trail": audit_df,
        "Audit Action Summary": audit_action_summary_df,
        "Audit User Summary": audit_user_summary_df,
    }
)

st.divider()
st.download_button(
    "Download Complete Excel Report",
    data=excel_file,
    file_name=f"{batch_label}_Batch_Report_Analysis.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

with st.expander("Extraction diagnostics"):
    st.write(
        {
            "PDF pages": len(pages),
            "Fill-weight records": len(fill_df),
            "Passed fill-weight records": len(fill_pass_df),
            "Safety alarm records": len(safety_df),
            "GMP alarm records": len(gmp_df),
            "Equipment alarm records": len(equipment_df),
            "Audit records": len(audit_df),
        }
    )
