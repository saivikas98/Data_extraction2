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
        # Unsorted text often preserves report table reading order better.
        raw_text = page.get_text("text", sort=False) or ""
        sorted_text = page.get_text("text", sort=True) or ""
        words = page.get_text("words", sort=True) or []
        pages.append(
            {
                "Page": page_number,
                "Text": raw_text,
                "Sorted Text": sorted_text,
                "Words": words,
            }
        )

    document.close()
    full_text = "\n".join(page["Text"] for page in pages)
    sorted_full_text = "\n".join(page["Sorted Text"] for page in pages)
    return full_text, sorted_full_text, pages


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
def extract_production_counters(text, sorted_text=""):
    columns = ["Counter Group", "Counter", "Value"]
    combined = f"{text}\n{sorted_text}"
    rows = []

    # The report stores labels first and their values below them, for example:
    # Infeed (pcs), Outfeed (pcs), Reject (pcs), followed by 1640, 1523, 117.
    section_patterns = [
        (
            "Production Counters",
            r"Production\s*Counters?\s*:\s*(.*?)\s*Check\s*Weigh\s*Reject\s*Counters?\s*:",
            ["Infeed (pcs)", "Outfeed (pcs)", "Reject (pcs)"],
        ),
        (
            "Check Weigh Reject Counters",
            r"Check\s*Weigh\s*Reject\s*Counters?\s*:\s*(.*?)\s*Air\s*Bubble\s*Reject\s*Counters?\s*:",
            ["Weigh Cell 1", "Weigh Cell 2", "Weigh Cell 3", "Weigh Cell 4", "Total"],
        ),
        (
            "Air Bubble Reject Counters",
            r"Air\s*Bubble\s*Reject\s*Counters?\s*:\s*(.*?)\s*Needle\s*Bonding\s*Reject\s*Counters?\s*:",
            ["Position 1", "Position 2", "Position 3", "Position 4", "Total"],
        ),
        (
            "Needle Bonding Reject Counters",
            r"Needle\s*Bonding\s*Reject\s*Counters?\s*:\s*(.*?)\s*Assembly\s*Reject\s*Counters?\s*:",
            ["Position 1", "Position 2", "Position 3", "Position 4", "Total"],
        ),
        (
            "Assembly Reject Counters",
            r"Assembly\s*Reject\s*Counters?\s*:\s*(.*?)\s*Filling\s*speed\s*:",
            ["Position 1", "Position 2", "Position 3", "Position 4", "Total"],
        ),
    ]

    for group, pattern, expected_labels in section_patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        section = match.group(1)
        values = [int(x.replace(",", "")) for x in re.findall(r"(?m)^\s*(\d[\d,]*)\s*$", section)]
        if len(values) >= len(expected_labels):
            values = values[-len(expected_labels):]
            for label, value in zip(expected_labels, values):
                rows.append({"Counter Group": group, "Counter": label, "Value": value})

    # Direct inline fallback for other report versions.
    if not any(row["Counter Group"] == "Production Counters" for row in rows):
        inline = re.search(
            r"Infeed\s*(?:\(pcs\))?\s*[:=-]?\s*(\d+)\s+"
            r"Outfeed\s*(?:\(pcs\))?\s*[:=-]?\s*(\d+)\s+"
            r"Reject\s*(?:\(pcs\))?\s*[:=-]?\s*(\d+)",
            combined,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if inline:
            for label, value in zip(
                ["Infeed (pcs)", "Outfeed (pcs)", "Reject (pcs)"],
                inline.groups(),
            ):
                rows.append(
                    {
                        "Counter Group": "Production Counters",
                        "Counter": label,
                        "Value": int(value),
                    }
                )

    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame = frame.drop_duplicates(
            subset=["Counter Group", "Counter"], keep="first"
        ).reset_index(drop=True)
    return frame


def extract_filling_speed(text, sorted_text=""):
    combined = f"{text}\n{sorted_text}"
    match = re.search(
        r"Filling\s*speed\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*([^\n]*)",
        combined,
        flags=re.IGNORECASE,
    )
    if not match:
        return pd.DataFrame(columns=["Parameter", "Value", "Unit"])

    value = float(match.group(1).replace(",", "."))
    unit = normalize_space(match.group(2)) or "syringes/min"
    return pd.DataFrame(
        [{"Parameter": "Filling Speed", "Value": value, "Unit": unit}]
    )


# =====================================================
# FILL WEIGHT TABLE
# =====================================================
def _parse_fill_weight_stream(text):
    rows = []
    # Whitespace includes line breaks, so this handles horizontal and vertically split rows.
    pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+"
        r"(-?\d+(?:[.,]\d+)?)\s+"
        r"(-?\d+(?:[.,]\d+)?)\s+"
        r"(-?\d+(?:[.,]\d+)?)\s+"
        r"([01])\s+"
        r"([01-])\s+"
        r"([01-])\s+"
        r"([01-])\s+"
        r"([1-4])(?=\s|$)",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        values = [match.group(i).replace(",", ".") for i in (3, 4, 5)]
        rows.append(
            {
                "Timestamp": f"{match.group(1)} {match.group(2)}",
                "Nett Weight": float(values[0]),
                "Gross Weight": float(values[1]),
                "Tare Weight": float(values[2]),
                "Weight Passed": int(match.group(6)),
                "Air Bubble": match.group(7),
                "Needle Bonding": match.group(8),
                "Assembly": match.group(9),
                "Position": int(match.group(10)),
            }
        )
    return rows


def _words_to_visual_lines(words, y_tolerance=2.5):
    """Rebuild visual rows from PyMuPDF words using y coordinates."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w[1] / y_tolerance), w[0]))
    lines = []
    current = []
    current_y = None
    for word in ordered:
        y = float(word[1])
        if current_y is None or abs(y - current_y) <= y_tolerance:
            current.append(word)
            current_y = y if current_y is None else (current_y + y) / 2
        else:
            lines.append(" ".join(str(w[4]) for w in sorted(current, key=lambda x: x[0])))
            current = [word]
            current_y = y
    if current:
        lines.append(" ".join(str(w[4]) for w in sorted(current, key=lambda x: x[0])))
    return lines


def extract_fill_weights(text, sorted_text="", pages=None):
    columns = [
        "Timestamp", "Nett Weight", "Gross Weight", "Tare Weight",
        "Weight Passed", "Air Bubble", "Needle Bonding", "Assembly", "Position",
    ]
    rows = []

    # Try both native PDF reading orders first.
    rows.extend(_parse_fill_weight_stream(text))
    rows.extend(_parse_fill_weight_stream(sorted_text))

    # Reconstruct visual lines from word coordinates when plain text order is column-wise.
    if pages:
        for page in pages:
            lower = f"{page.get('Text', '')} {page.get('Sorted Text', '')}".lower()
            is_candidate = any(
                key in lower
                for key in ["nett weight", "net weight", "gross weight", "tare weight", "weight passed"]
            )
            if not is_candidate:
                continue
            visual_text = "\n".join(_words_to_visual_lines(page.get("Words", [])))
            rows.extend(_parse_fill_weight_stream(visual_text))

    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["Timestamp"] = pd.to_datetime(frame["Timestamp"], dayfirst=True, errors="coerce")
        frame = (
            frame.dropna(subset=["Timestamp"])
            .drop_duplicates()
            .sort_values("Timestamp")
            .reset_index(drop=True)
        )
    return frame


# =====================================================
# ALARM EXTRACTION
# =====================================================
def clean_alarm_chunk(text):
    """Remove repeating column headers and page footers before parsing alarms."""
    # The footer begins with Batch Number on every alarm page. Anything after that
    # is report metadata and must never become part of an alarm message.
    text = re.split(r"(?im)^\s*Batch\s+Number\s*:", text, maxsplit=1)[0]
    text = re.sub(r"(?im)^\s*(Timestamp|State|MsgText|Code)\s*$", "", text)
    text = re.sub(r"(?im)^\s*Page\s+\d+\s+of\s+\d+\s*$", "", text)
    return text.strip()


def parse_alarm_records(section_text, alarm_type):
    columns = ["Timestamp", "State", "Message", "Code", "Alarm Type"]
    rows = []
    section_text = clean_alarm_chunk(section_text)
    if not section_text:
        return pd.DataFrame(columns=columns)

    # A record is exactly: timestamp, state, message, integer code. The lookahead
    # constrains the message to end before the next timestamp or end of clean chunk.
    record_pattern = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+"
        r"([+*\-])\s+"
        r"(.+?)\s+"
        r"(\d{1,5})\s*"
        r"(?=(?:\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s+[+*\-])|$)",
        flags=re.DOTALL,
    )

    for match in record_pattern.finditer(section_text):
        message = normalize_space(match.group(4))
        # Defensive removal of any metadata if a report variant puts footer text inline.
        message = re.split(
            r"\b(?:Batch Number|Product Name|Report Generated|Printed by|Page \d+ of \d+)\s*:",
            message,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        message = normalize_space(message)
        if not message:
            continue
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
        frame = (
            frame.dropna(subset=["Timestamp"])
            .drop_duplicates()
            .sort_values("Timestamp")
            .reset_index(drop=True)
        )
    return frame


def extract_alarm_tables(pages):
    """Parse Safety, GMP and Equipment sections page-by-page with header state."""
    category_aliases = [
        ("Safety", re.compile(r"(?im)^\s*(?:Alarms?\s+Safety|Safety\s+Alarms?)\s*$")),
        ("GMP", re.compile(r"(?im)^\s*(?:Alarms?\s+GMP|GMP\s+Alarms?)\s*$")),
        ("Equipment", re.compile(r"(?im)^\s*(?:Equipment\s+Alarms?|Alarms?\s+Equipment)\s*$")),
    ]
    chunks = {"Safety": [], "GMP": [], "Equipment": []}
    current_category = None

    for page in pages:
        page_text = page.get("Text", "") or ""
        # Remove the footer separately on every page. This fixes the observed case
        # where "Batch Number: ..." was appended to the final message on a page.
        page_text = re.split(
            r"(?im)^\s*Batch\s+Number\s*:", page_text, maxsplit=1
        )[0]

        # A page can contain the end of one category and the beginning of another.
        header_hits = []
        for category, header_pattern in category_aliases:
            for hit in header_pattern.finditer(page_text):
                header_hits.append((hit.start(), hit.end(), category))
        header_hits.sort(key=lambda item: item[0])

        cursor = 0
        for hit_start, hit_end, new_category in header_hits:
            if current_category and hit_start > cursor:
                chunks[current_category].append(page_text[cursor:hit_start])
            current_category = new_category
            cursor = hit_end
        if current_category and cursor < len(page_text):
            chunks[current_category].append(page_text[cursor:])

        # Stop carrying alarm state when the audit section begins.
        if re.search(r"(?im)^\s*Audit\s+Trail\s*$", page_text):
            current_category = None

    outputs = {}
    for category in ["Safety", "GMP", "Equipment"]:
        category_frames = []
        for chunk in chunks[category]:
            parsed = parse_alarm_records(chunk, category)
            if not parsed.empty:
                category_frames.append(parsed)
        if category_frames:
            outputs[category] = (
                pd.concat(category_frames, ignore_index=True)
                .drop_duplicates()
                .sort_values("Timestamp")
                .reset_index(drop=True)
            )
        else:
            outputs[category] = empty_dataframe(
                ["Timestamp", "State", "Message", "Code", "Alarm Type"]
            )
    return outputs["Safety"], outputs["GMP"], outputs["Equipment"]


def extract_safety_alarms(pages):
    return extract_alarm_tables(pages)[0]


def extract_gmp_alarms(pages):
    return extract_alarm_tables(pages)[1]


def extract_equipment_alarms(pages):
    return extract_alarm_tables(pages)[2]


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
# INTERACTIVE PLOT CONFIGURATION
# =====================================================
PLOTLY_CONFIG = {
    "displaylogo": False,
    "scrollZoom": True,
    "displayModeBar": True,
    "editable": True,
    "edits": {
        "axisTitleText": True,
        "legendText": True,
        "annotationText": True,
        "titleText": True,
    },
    "toImageButtonOptions": {
        "format": "png",
        "filename": "interactive_graph",
        "height": 700,
        "width": 1400,
        "scale": 2,
    },
}


def make_interactive(fig, show_x_grid=True, show_y_grid=True, show_range_slider=False):
    # Keep only layout properties supported across older and newer Plotly versions.
    # Graph editing is enabled through the Plotly config passed to st.plotly_chart.
    fig.update_layout(
        dragmode="zoom",
        hovermode="closest",
        clickmode="event+select",
        selectdirection="any",
        uirevision="keep-user-zoom",
    )
    fig.update_xaxes(
        showgrid=show_x_grid,
        rangeslider_visible=show_range_slider,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
    )
    fig.update_yaxes(
        showgrid=show_y_grid,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
    )
    return fig


def figure_to_html_bytes(fig):
    return fig.to_html(
        full_html=True,
        include_plotlyjs=True,
        config=PLOTLY_CONFIG,
    ).encode("utf-8")


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
        full_text, sorted_full_text, pages = extract_pdf_pages(pdf_bytes)
        summary_df = extract_batch_summary(full_text)
        production_df = extract_production_counters(full_text, sorted_full_text)
        filling_speed_df = extract_filling_speed(full_text, sorted_full_text)
        fill_df = extract_fill_weights(full_text, sorted_full_text, pages)
        safety_df, gmp_df, equipment_df = extract_alarm_tables(pages)
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
st.sidebar.subheader("Interactive Graph Controls")
show_x_grid = st.sidebar.checkbox("Show X-axis gridlines", value=True)
show_y_grid = st.sidebar.checkbox("Show Y-axis gridlines", value=True)
show_range_slider = st.sidebar.checkbox("Show X-axis range slider", value=True)
marker_size = st.sidebar.slider("Scatter marker size", 3, 16, 7)
marker_opacity = st.sidebar.slider("Scatter marker opacity", 0.2, 1.0, 0.82, 0.02)

if lower_limit >= upper_limit:
    st.sidebar.error("Lower limit must be less than upper limit.")

alarm_frames = [df for df in [safety_df, gmp_df, equipment_df] if not df.empty]
all_alarms_df = pd.concat(alarm_frames, ignore_index=True) if alarm_frames else empty_dataframe(["Timestamp", "State", "Message", "Code", "Alarm Type"])
if not all_alarms_df.empty:
    all_alarms_df = all_alarms_df.sort_values("Timestamp").reset_index(drop=True)
all_alarm_summary_df = create_alarm_summary(all_alarms_df)

(
    summary_tab,
    production_tab,
    fill_tab,
    alarms_tab,
    audit_tab,
) = st.tabs(
    ["Summary", "Production", "Fill Weight", "All Alarms", "Audit Trail"]
)

with summary_tab:
    st.subheader("Batch Summary")
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    total_fill_records = len(fill_df)
    passed_fill_records = len(fill_pass_df)
    rejected_fill_records = total_fill_records - passed_fill_records
    pass_rate = passed_fill_records / total_fill_records * 100 if total_fill_records else 0.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Fill Records", f"{total_fill_records:,}")
    c2.metric("Fill Weight Passed", f"{passed_fill_records:,}")
    c3.metric("Fill Weight Not Passed", f"{rejected_fill_records:,}")
    c4.metric("Pass Rate", f"{pass_rate:.2f}%")
    a1, a2, a3, a4 = st.columns(4)
    all_alarm_count = len(safety_df) + len(gmp_df) + len(equipment_df)
    a1.metric("All Alarm Events", f"{all_alarm_count:,}")
    a2.metric("Audit Records", f"{len(audit_df):,}")
    a3.metric("PDF Pages", f"{len(pages):,}")
    if not filling_speed_df.empty:
        speed_value = filling_speed_df.iloc[0]["Value"]
        speed_unit = filling_speed_df.iloc[0]["Unit"]
        a4.metric("Filling Speed", f"{speed_value:g} {speed_unit}")
    else:
        a4.metric("Filling Speed", "Not detected")

with production_tab:
    st.subheader("Filling Speed")
    if filling_speed_df.empty:
        st.warning("Filling speed was not detected in the report.")
    else:
        filling_speed_value = filling_speed_df.iloc[0]["Value"]
        filling_speed_unit = filling_speed_df.iloc[0]["Unit"]
        st.metric("Filling Speed", f"{filling_speed_value:g} {filling_speed_unit}")
        st.dataframe(filling_speed_df, use_container_width=True, hide_index=True)

    st.subheader("Production Counters")
    if production_df.empty:
        st.warning("Production counter labels or values were not recognized. See Extraction diagnostics for the detected page text.")
    else:
        st.dataframe(production_df, use_container_width=True, hide_index=True)
        production_group = st.selectbox(
            "Counter group to plot",
            options=production_df["Counter Group"].drop_duplicates().tolist(),
        )
        production_plot_df = production_df[
            production_df["Counter Group"].eq(production_group)
        ]
        production_fig = px.bar(
            production_plot_df,
            x="Counter",
            y="Value",
            color="Counter",
            text="Value",
            title=production_group,
        )
        production_fig.update_traces(textposition="outside")
        production_fig = apply_graph_style(
            production_fig,
            production_group,
            "Counter",
            "Value",
        )
        production_fig = make_interactive(
            production_fig,
            show_x_grid=show_x_grid,
            show_y_grid=show_y_grid,
            show_range_slider=False,
        )
        st.plotly_chart(
            production_fig,
            use_container_width=True,
            config=PLOTLY_CONFIG,
            key="interactive_production_counters",
        )
        st.download_button(
            "Download Interactive Production Counter Chart (HTML)",
            data=figure_to_html_bytes(production_fig),
            file_name=f"{batch_label}_production_counters.html",
            mime="text/html",
            use_container_width=True,
        )

with fill_tab:
    st.subheader("Fill Weight Passed Records")
    if fill_df.empty:
        st.warning("Fill-weight rows were not recognized. The parser tried native text, sorted text and coordinate-based row reconstruction.")
    else:
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Passed Records", f"{len(fill_pass_df):,}")
        f2.metric("Mean Nett Weight", f"{fill_pass_df['Nett Weight'].mean():.2f}" if not fill_pass_df.empty else "N/A")
        f3.metric("Standard Deviation", f"{fill_pass_df['Nett Weight'].std():.2f}" if len(fill_pass_df) > 1 else "N/A")
        f4.metric("Range", f"{fill_pass_df['Nett Weight'].min():.2f} to {fill_pass_df['Nett Weight'].max():.2f}" if not fill_pass_df.empty else "N/A")
        table_choice = st.radio("Fill weight table", ["Passed only", "All records"], horizontal=True)
        shown = fill_pass_df if table_choice == "Passed only" else fill_df
        st.dataframe(shown, use_container_width=True, hide_index=True)
        if not fill_pass_df.empty and lower_limit < upper_limit:
            st.caption(
                "Use the Plotly toolbar to zoom, pan, box-select, lasso-select, draw annotations, "
                "reset axes, and download a PNG. Double-click the chart to reset the view."
            )

            available_positions = sorted(fill_pass_df["Position"].dropna().unique().tolist())
            selected_positions = st.multiselect(
                "Positions to display",
                options=available_positions,
                default=available_positions,
            )
            graph_df = fill_pass_df[fill_pass_df["Position"].isin(selected_positions)].copy()

            st.markdown("#### Scatter Plot Axis Limits")
            axis_col_1, axis_col_2 = st.columns(2)
            with axis_col_1:
                auto_x_limits = st.checkbox("Automatic X-axis limits", value=True)
                default_x_min = 1.0
                default_x_max = float(max(len(graph_df), 1))
                scatter_x_min = st.number_input(
                    "X-axis minimum",
                    value=default_x_min,
                    disabled=auto_x_limits,
                    key="scatter_x_min",
                )
                scatter_x_max = st.number_input(
                    "X-axis maximum",
                    value=default_x_max,
                    disabled=auto_x_limits,
                    key="scatter_x_max",
                )
            with axis_col_2:
                auto_y_limits = st.checkbox("Automatic Y-axis limits", value=True)
                if not graph_df.empty:
                    default_y_min = float(graph_df["Nett Weight"].min()) - 1.0
                    default_y_max = float(graph_df["Nett Weight"].max()) + 1.0
                else:
                    default_y_min, default_y_max = 520.0, 550.0
                scatter_y_min = st.number_input(
                    "Y-axis minimum",
                    value=default_y_min,
                    disabled=auto_y_limits,
                    key="scatter_y_min",
                )
                scatter_y_max = st.number_input(
                    "Y-axis maximum",
                    value=default_y_max,
                    disabled=auto_y_limits,
                    key="scatter_y_max",
                )

            invalid_axis_limits = (
                (not auto_x_limits and scatter_x_min >= scatter_x_max)
                or (not auto_y_limits and scatter_y_min >= scatter_y_max)
            )
            if invalid_axis_limits:
                st.error("Axis minimum must be lower than axis maximum.")

            if graph_df.empty:
                st.warning("Select at least one filling position to display the graphs.")
            elif invalid_axis_limits:
                st.warning("Correct the scatter-plot axis limits to display the graphs.")
            else:
                scatter_figure = make_fill_scatter(
                    graph_df,
                    batch_label,
                    lower_limit,
                    upper_limit,
                )
                scatter_figure.update_traces(
                    marker=dict(size=marker_size, opacity=marker_opacity)
                )
                scatter_figure = make_interactive(
                    scatter_figure,
                    show_x_grid=show_x_grid,
                    show_y_grid=show_y_grid,
                    show_range_slider=False,
                )

                # Apply user-defined visible axis limits to the scatter plot.
                if not auto_x_limits:
                    scatter_figure.update_xaxes(range=[scatter_x_min, scatter_x_max])
                if not auto_y_limits:
                    scatter_figure.update_yaxes(range=[scatter_y_min, scatter_y_max])

                st.plotly_chart(
                    scatter_figure,
                    use_container_width=True,
                    config=PLOTLY_CONFIG,
                    key="interactive_fill_scatter",
                )
                st.download_button(
                    "Download Interactive Fill-Weight Graph (HTML)",
                    data=figure_to_html_bytes(scatter_figure),
                    file_name=f"{batch_label}_interactive_fill_weight.html",
                    mime="text/html",
                    use_container_width=True,
                )

                histogram_figure = make_fill_histogram(
                    graph_df,
                    batch_label,
                    histogram_bins,
                )
                histogram_figure = make_interactive(
                    histogram_figure,
                    show_x_grid=show_x_grid,
                    show_y_grid=show_y_grid,
                    show_range_slider=False,
                )
                st.plotly_chart(
                    histogram_figure,
                    use_container_width=True,
                    config=PLOTLY_CONFIG,
                    key="interactive_fill_histogram",
                )
                st.download_button(
                    "Download Interactive Histogram (HTML)",
                    data=figure_to_html_bytes(histogram_figure),
                    file_name=f"{batch_label}_interactive_histogram.html",
                    mime="text/html",
                    use_container_width=True,
                )

with alarms_tab:
    st.subheader("Combined Alarm Table")
    if all_alarms_df.empty:
        st.warning("No structured alarm records were detected.")
    else:
        al1, al2, al3, al4 = st.columns(4)
        al1.metric("Total Alarm Events", f"{len(all_alarms_df):,}")
        al2.metric("Unique Alarm Types", f"{len(all_alarm_summary_df):,}")
        al3.metric("Repeated Alarm Types", f"{int((all_alarm_summary_df['Occurrences'] > 1).sum()):,}")
        al4.metric("Alarm Categories", f"{all_alarms_df['Alarm Type'].nunique():,}")
        category_filter = st.multiselect(
            "Alarm categories",
            options=sorted(all_alarms_df["Alarm Type"].dropna().unique().tolist()),
            default=sorted(all_alarms_df["Alarm Type"].dropna().unique().tolist()),
        )
        filtered_alarms = all_alarms_df[all_alarms_df["Alarm Type"].isin(category_filter)]
        st.dataframe(filtered_alarms, use_container_width=True, hide_index=True)
        filtered_summary = create_alarm_summary(filtered_alarms)
        st.subheader("Combined Alarm Frequency Summary")
        st.dataframe(filtered_summary, use_container_width=True, hide_index=True)
        top_alarm = filtered_summary.head(20).sort_values("Occurrences")
        if not top_alarm.empty:
            fig_alarm = px.bar(top_alarm, x="Occurrences", y="Message", orientation="h", color_discrete_sequence=["#d9534f"], hover_data=["Code"])
            fig_alarm = apply_graph_style(
                fig_alarm,
                "Top Alarms by Frequency",
                "Occurrences",
                "Alarm Message",
            )
            fig_alarm = make_interactive(
                fig_alarm,
                show_x_grid=show_x_grid,
                show_y_grid=show_y_grid,
                show_range_slider=False,
            )
            st.plotly_chart(
                fig_alarm,
                use_container_width=True,
                config=PLOTLY_CONFIG,
                key="interactive_alarm_pareto",
            )
            st.download_button(
                "Download Interactive Alarm Chart (HTML)",
                data=figure_to_html_bytes(fig_alarm),
                file_name=f"{batch_label}_interactive_alarm_pareto.html",
                mime="text/html",
                use_container_width=True,
            )

        if not filtered_alarms.empty:
            st.subheader("Alarm Events by Category")
            category_counts = (
                filtered_alarms.groupby("Alarm Type")
                .size()
                .reset_index(name="Occurrences")
            )
            category_fig = px.bar(
                category_counts,
                x="Alarm Type",
                y="Occurrences",
                color="Alarm Type",
                text="Occurrences",
            )
            category_fig.update_traces(textposition="outside")
            category_fig = apply_graph_style(
                category_fig,
                "Alarm Events by Category",
                "Alarm Category",
                "Occurrences",
            )
            category_fig = make_interactive(
                category_fig,
                show_x_grid=show_x_grid,
                show_y_grid=show_y_grid,
                show_range_slider=False,
            )
            st.plotly_chart(
                category_fig,
                use_container_width=True,
                config=PLOTLY_CONFIG,
                key="interactive_alarm_category",
            )

            st.subheader("Alarm Timeline")
            timeline_df = filtered_alarms.dropna(subset=["Timestamp"]).copy()
            if not timeline_df.empty:
                timeline_df["Hour"] = timeline_df["Timestamp"].dt.floor("h")
                timeline_counts = (
                    timeline_df.groupby(["Hour", "Alarm Type"])
                    .size()
                    .reset_index(name="Occurrences")
                )
                timeline_fig = px.line(
                    timeline_counts,
                    x="Hour",
                    y="Occurrences",
                    color="Alarm Type",
                    markers=True,
                )
                timeline_fig = apply_graph_style(
                    timeline_fig,
                    "Alarm Events Over Time",
                    "Time",
                    "Occurrences",
                )
                timeline_fig = make_interactive(
                    timeline_fig,
                    show_x_grid=show_x_grid,
                    show_y_grid=show_y_grid,
                    show_range_slider=show_range_slider,
                )
                st.plotly_chart(
                    timeline_fig,
                    use_container_width=True,
                    config=PLOTLY_CONFIG,
                    key="interactive_alarm_timeline",
                )

with audit_tab:
    st.subheader("Audit Trail Table")
    if audit_df.empty:
        st.warning("No structured audit-trail records were detected.")
    else:
        au1, au2, au3 = st.columns(3)
        au1.metric("Total Audit Records", f"{len(audit_df):,}")
        au2.metric("Unique Actions", f"{len(audit_action_summary_df):,}")
        au3.metric("Repeated Actions", f"{int((audit_action_summary_df['Occurrences'] > 1).sum()):,}")
        st.dataframe(audit_df, use_container_width=True, hide_index=True)
        st.subheader("Audit Action Frequency Summary")
        st.dataframe(audit_action_summary_df, use_container_width=True, hide_index=True)
        if not audit_user_summary_df.empty:
            st.subheader("Audit Records by User")
            st.dataframe(audit_user_summary_df, use_container_width=True, hide_index=True)
            audit_user_fig = px.bar(
                audit_user_summary_df.head(20),
                x="User",
                y="Occurrences",
                color="User",
                text="Occurrences",
            )
            audit_user_fig.update_traces(textposition="outside")
            audit_user_fig = apply_graph_style(
                audit_user_fig,
                "Audit Records by User",
                "User",
                "Occurrences",
            )
            audit_user_fig = make_interactive(
                audit_user_fig,
                show_x_grid=show_x_grid,
                show_y_grid=show_y_grid,
                show_range_slider=False,
            )
            st.plotly_chart(
                audit_user_fig,
                use_container_width=True,
                config=PLOTLY_CONFIG,
                key="interactive_audit_users",
            )

        top_audit_actions = audit_action_summary_df.head(20).sort_values("Occurrences")
        if not top_audit_actions.empty:
            audit_action_fig = px.bar(
                top_audit_actions,
                x="Occurrences",
                y="Action",
                orientation="h",
                color_discrete_sequence=["#5b9bd5"],
            )
            audit_action_fig = apply_graph_style(
                audit_action_fig,
                "Top Audit Actions by Frequency",
                "Occurrences",
                "Audit Action",
            )
            audit_action_fig = make_interactive(
                audit_action_fig,
                show_x_grid=show_x_grid,
                show_y_grid=show_y_grid,
                show_range_slider=False,
            )
            st.plotly_chart(
                audit_action_fig,
                use_container_width=True,
                config=PLOTLY_CONFIG,
                key="interactive_audit_actions",
            )
            st.download_button(
                "Download Interactive Audit Action Chart (HTML)",
                data=figure_to_html_bytes(audit_action_fig),
                file_name=f"{batch_label}_audit_actions.html",
                mime="text/html",
                use_container_width=True,
            )

excel_file = build_excel(
    {
        "Batch Summary": summary_df,
        "Production Counters": production_df,
        "Filling Speed": filling_speed_df,
        "Fill Weight All": fill_df,
        "Fill Weight Passed": fill_pass_df,
        "All Alarms": all_alarms_df,
        "All Alarm Summary": all_alarm_summary_df,
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
