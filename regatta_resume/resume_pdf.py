from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
import pandas as pd

def _blank(val):
    return "" if (val is None or (isinstance(val, float) and pd.isna(val))) else str(val)

def _fmt_intish(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        f = float(val)
        return str(int(f)) if f.is_integer() else str(val)
    except Exception:
        return str(val)

def _first3(val):
    s = _blank(val).strip()
    return s[:3] if s else ""

def create_regatta_resume_pdf_classic(sailor_name, scraper_df, results_df, filename="regatta_resume.pdf"):
    """
    scraper_df -> Techscore section
    results_df -> Clubspot section (handles either 'raw' Clubspot schema or 'shaped' schema)
    """
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # --- Title ---
    elements.append(Paragraph(f"<b>{_blank(sailor_name)}'s Regatta Résumé</b>", styles['Title']))
    elements.append(Spacer(1, 12))

    # --- Techscore Regattas (scraper_df) ---
    if scraper_df is not None and not scraper_df.empty:
        sdf = scraper_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Techscore Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        ts_table = [["Class", "Regatta", "Date", "Place", "Position"]]
        for _, row in sdf.iterrows():
            ts_table.append([
                _blank(row.get("Source", "")),
                Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                _blank(row.get("Date", "")),
                _fmt_intish(row.get("Place", "")),
                _blank(row.get("Result", "")),
            ])

        t = Table(ts_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 18))

    # --- Clubspot Regattas (results_df) ---
    if results_df is not None and not results_df.empty:
        cdf = results_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Clubspot Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        # Detect schema: 'shaped' (Source/Regatta/Date/Place/Result) vs 'raw' (Regatta Name/Club/Start Date (UTC)/Matched Row Text)
        shaped = {"Source","Regatta","Date","Place","Result"}.issubset(set(cdf.columns))
        raw    = {"Regatta Name","Start Date (UTC)","Matched Row Text"}.issubset(set(cdf.columns))

        cs_table = [["Class", "Regatta", "Date", "Place", "Position"]]

        if shaped:
            # Use shaped columns; Class from Source; Place & Position = first 3 chars of Result (to match your HTML shaping)
            for _, row in cdf.iterrows():
                result_first3 = _first3(row.get("Result", ""))
                cs_table.append([
                    _blank(row.get("Source", "")),                         # Class
                    Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                    _blank(row.get("Date", "")),
                    result_first3,                                         # Place (first 3)
                    result_first3,                                         # Position (first 3)
                ])
        elif raw:
            # Map raw Clubspot to shaped-like: Class blank (user can edit later in app),
            # Regatta from 'Regatta Name', Date from 'Start Date (UTC)',
            # Place & Position from first 3 chars of 'Matched Row Text'
            for _, row in cdf.iterrows():
                first3 = _first3(row.get("Matched Row Text", ""))
                cs_table.append([
                    "",  # Class (blank; user-editable in app)
                    Paragraph(_blank(row.get("Regatta Name", "")), styles["Normal"]),
                    _blank(row.get("Start Date (UTC)", "")),
                    first3,  # Place (first 3 of result text)
                    first3,  # Position (first 3 of result text)
                ])
        else:
            # Fallback: try best-effort column names
            for _, row in cdf.iterrows():
                reg = row.get("Regatta") or row.get("Regatta Name") or ""
                date = row.get("Date") or row.get("Start Date (UTC)") or ""
                res = row.get("Result") or row.get("Matched Row Text") or ""
                first3 = _first3(res)
                cs_table.append([
                    _blank(row.get("Source", "")),
                    Paragraph(_blank(reg), styles["Normal"]),
                    _blank(date),
                    first3,
                    first3,
                ])

        t = Table(cs_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(t)

    doc.build(elements)
def create_regatta_resume_pdf_modern(sailor_name, scraper_df, results_df, filename="regatta_resume.pdf"):
    """
    scraper_df -> Techscore section
    results_df -> Clubspot section (handles either 'raw' Clubspot schema or 'shaped' schema)
    """
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # --- Title ---
    elements.append(Paragraph(f"<b>{_blank(sailor_name)}'s Regatta Résumé</b>", styles['Title']))
    elements.append(Spacer(1, 12))

    # --- Techscore Regattas (scraper_df) ---
    if scraper_df is not None and not scraper_df.empty:
        sdf = scraper_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Techscore Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        ts_table = [["Class", "Regatta", "Date", "Place", "Position"]]
        for _, row in sdf.iterrows():
            ts_table.append([
                _blank(row.get("Source", "")),
                Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                _blank(row.get("Date", "")),
                _fmt_intish(row.get("Place", "")),
                _blank(row.get("Result", "")),
            ])

        t = Table(ts_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 18))

    # --- Clubspot Regattas (results_df) ---
    if results_df is not None and not results_df.empty:
        cdf = results_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Clubspot Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        # Detect schema: 'shaped' (Source/Regatta/Date/Place/Result) vs 'raw' (Regatta Name/Club/Start Date (UTC)/Matched Row Text)
        shaped = {"Source","Regatta","Date","Place","Result"}.issubset(set(cdf.columns))
        raw    = {"Regatta Name","Start Date (UTC)","Matched Row Text"}.issubset(set(cdf.columns))

        cs_table = [["Class", "Regatta", "Date", "Place", "Position"]]

        if shaped:
            # Use shaped columns; Class from Source; Place & Position = first 3 chars of Result (to match your HTML shaping)
            for _, row in cdf.iterrows():
                result_first3 = _first3(row.get("Result", ""))
                cs_table.append([
                    _blank(row.get("Source", "")),                         # Class
                    Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                    _blank(row.get("Date", "")),
                    result_first3,                                         # Place (first 3)
                    result_first3,                                         # Position (first 3)
                ])
        elif raw:
            # Map raw Clubspot to shaped-like: Class blank (user can edit later in app),
            # Regatta from 'Regatta Name', Date from 'Start Date (UTC)',
            # Place & Position from first 3 chars of 'Matched Row Text'
            for _, row in cdf.iterrows():
                first3 = _first3(row.get("Matched Row Text", ""))
                cs_table.append([
                    "",  # Class (blank; user-editable in app)
                    Paragraph(_blank(row.get("Regatta Name", "")), styles["Normal"]),
                    _blank(row.get("Start Date (UTC)", "")),
                    first3,  # Place (first 3 of result text)
                    first3,  # Position (first 3 of result text)
                ])
        else:
            # Fallback: try best-effort column names
            for _, row in cdf.iterrows():
                reg = row.get("Regatta") or row.get("Regatta Name") or ""
                date = row.get("Date") or row.get("Start Date (UTC)") or ""
                res = row.get("Result") or row.get("Matched Row Text") or ""
                first3 = _first3(res)
                cs_table.append([
                    _blank(row.get("Source", "")),
                    Paragraph(_blank(reg), styles["Normal"]),
                    _blank(date),
                    first3,
                    first3,
                ])

        t = Table(cs_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(t)

    doc.build(elements)

def create_regatta_resume_pdf_minimalist(sailor_name, scraper_df, results_df, filename="regatta_resume.pdf"):
    """
    scraper_df -> Techscore section
    results_df -> Clubspot section (handles either 'raw' Clubspot schema or 'shaped' schema)
    """
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # --- Title ---
    elements.append(Paragraph(f"<b>{_blank(sailor_name)}'s Regatta Résumé</b>", styles['Title']))
    elements.append(Spacer(1, 12))

    # --- Techscore Regattas (scraper_df) ---
    if scraper_df is not None and not scraper_df.empty:
        sdf = scraper_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Techscore Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        ts_table = [["Class", "Regatta", "Date", "Place", "Position"]]
        for _, row in sdf.iterrows():
            ts_table.append([
                _blank(row.get("Source", "")),
                Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                _blank(row.get("Date", "")),
                _fmt_intish(row.get("Place", "")),
                _blank(row.get("Result", "")),
            ])

        t = Table(ts_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 18))

    # --- Clubspot Regattas (results_df) ---
    if results_df is not None and not results_df.empty:
        cdf = results_df.dropna(how="all").copy()

        elements.append(Paragraph("<b>Clubspot Regattas</b>", styles['Heading2']))
        elements.append(Spacer(1, 6))

        # Detect schema: 'shaped' (Source/Regatta/Date/Place/Result) vs 'raw' (Regatta Name/Club/Start Date (UTC)/Matched Row Text)
        shaped = {"Source","Regatta","Date","Place","Result"}.issubset(set(cdf.columns))
        raw    = {"Regatta Name","Start Date (UTC)","Matched Row Text"}.issubset(set(cdf.columns))

        cs_table = [["Class", "Regatta", "Date", "Place", "Position"]]

        if shaped:
            # Use shaped columns; Class from Source; Place & Position = first 3 chars of Result (to match your HTML shaping)
            for _, row in cdf.iterrows():
                result_first3 = _first3(row.get("Result", ""))
                cs_table.append([
                    _blank(row.get("Source", "")),                         # Class
                    Paragraph(_blank(row.get("Regatta", "")), styles["Normal"]),
                    _blank(row.get("Date", "")),
                    result_first3,                                         # Place (first 3)
                    result_first3,                                         # Position (first 3)
                ])
        elif raw:
            # Map raw Clubspot to shaped-like: Class blank (user can edit later in app),
            # Regatta from 'Regatta Name', Date from 'Start Date (UTC)',
            # Place & Position from first 3 chars of 'Matched Row Text'
            for _, row in cdf.iterrows():
                first3 = _first3(row.get("Matched Row Text", ""))
                cs_table.append([
                    "",  # Class (blank; user-editable in app)
                    Paragraph(_blank(row.get("Regatta Name", "")), styles["Normal"]),
                    _blank(row.get("Start Date (UTC)", "")),
                    first3,  # Place (first 3 of result text)
                    first3,  # Position (first 3 of result text)
                ])
        else:
            # Fallback: try best-effort column names
            for _, row in cdf.iterrows():
                reg = row.get("Regatta") or row.get("Regatta Name") or ""
                date = row.get("Date") or row.get("Start Date (UTC)") or ""
                res = row.get("Result") or row.get("Matched Row Text") or ""
                first3 = _first3(res)
                cs_table.append([
                    _blank(row.get("Source", "")),
                    Paragraph(_blank(reg), styles["Normal"]),
                    _blank(date),
                    first3,
                    first3,
                ])

        t = Table(cs_table, colWidths=[60, 220, 80, 40, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(t)

    doc.build(elements)
