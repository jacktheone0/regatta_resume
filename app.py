# app.py
from flask import Flask, render_template, request, send_file, send_from_directory, url_for, redirect, Response, stream_with_context
from pathlib import Path
import sys
import subprocess
import pandas as pd  
from flask import Response, stream_with_context
import os
import time
import subprocess
import sys
from scraper import scrape_all_sites, expand_result_fields
from resume_pdf import create_regatta_resume_pdf_classic, create_regatta_resume_pdf_modern, create_regatta_resume_pdf_minimalist
from flask import jsonify
import json
import weasyprint
from weasyprint import HTML

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
TEAMS_CSV = BASE_DIR / "teams.csv"

SCRAPER_SCRIPT = BASE_DIR / "Resume.py"
RESULTS_CSV = BASE_DIR / "results.csv"
PDF_PATH = BASE_DIR / "static" / "resume.pdf"
PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
SCRAPER_DF_PATH = BASE_DIR / "scraper_df.csv"
# New: directory to hold pre-built/static resumes
RESUMES_DIR = BASE_DIR / "static" / "resumes"
RESUMES_DIR.mkdir(parents=True, exist_ok=True)

def load_shaped_results(csv_path: Path) -> pd.DataFrame:
    """
    Load results.csv in its raw scraper shape and project to a unified, editable view:
    columns: [Source, Regatta, Date, Place, Result].
    We also attach a stable RowID based on current row order.
    """
    df = pd.read_csv(csv_path)

    reg_col  = 'Regatta' if 'Regatta' in df.columns else ('Regatta Name' if 'Regatta Name' in df.columns else None)
    date_col = 'Date' if 'Date' in df.columns else ('Start Date (UTC)' if 'Start Date (UTC)' in df.columns else None)
    txt_col  = 'Matched Row Text' if 'Matched Row Text' in df.columns else None

    if reg_col is None:
        df['__Regatta__'] = ''
        reg_col = '__Regatta__'
    if date_col is None:
        df['__Date__'] = ''
        date_col = '__Date__'
    if txt_col is None:
        df['__RowText__'] = ''
        txt_col = '__RowText__'

    parsed = pd.to_datetime(df[date_col], errors='coerce', utc=True)
    date_out = parsed.dt.strftime('%Y-%m-%d').fillna(df[date_col].astype(str))
    clip3 = df[txt_col].astype(str).str.strip().str[:3]

    shaped = pd.DataFrame({
        'RowID':  range(len(df)),
        'Source': '',
        'Regatta': df[reg_col],
        'Date':    date_out,
        'Place':   clip3,
        'Result':  clip3
    })
    return shaped

def write_shaped_results(csv_path: Path, shaped_df: pd.DataFrame) -> None:
    """
    Persist the SHAPED view back to csv_path. This overwrites with columns
    [Source, Regatta, Date, Place, Result]. It’s simple and consistent.
    """
    cols = ['Source','Regatta','Date','Place','Result']
    shaped_df.loc[:, cols].to_csv(csv_path, index=False)

# ---------------------------
# Routes
# ---------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        sailor_name = (request.form.get("sailor_name") or "").strip()
        start_date  = (request.form.get("start_date") or "").strip()
        end_date    = (request.form.get("end_date") or "").strip()
        max_regs    = (request.form.get("max_regattas") or "").strip()
        contains    = (request.form.get("filter") or "").strip()

        if not sailor_name:
            return render_template("index.html", error="Please enter a sailor's name.")

        # 👉 Do NOT run anything here. Redirect to the loading page with query params.
        return redirect(
            url_for(
                "loading_page",
                sailor_name=sailor_name,
                start_date=start_date,
                end_date=end_date,
                max_regattas=max_regs,
                filter=contains
            )
        )

    return render_template("index.html", error=None)

# NEW: map results.csv to the columns your Jinja template expects
def rows_from_results_csv(csv_path: Path):
    df = pd.read_csv(csv_path)

    # Column fallbacks (handles either your scraper's names or already-shaped names)
    reg_col  = 'Regatta' if 'Regatta' in df.columns else ('Regatta Name' if 'Regatta Name' in df.columns else None)
    date_col = 'Date' if 'Date' in df.columns else ('Start Date (UTC)' if 'Start Date (UTC)' in df.columns else None)
    txt_col  = 'Matched Row Text' if 'Matched Row Text' in df.columns else None

    # Graceful handling if any expected column is missing
    if reg_col is None:
        df['__Regatta__'] = ''
        reg_col = '__Regatta__'
    if date_col is None:
        df['__Date__'] = ''
        date_col = '__Date__'
    if txt_col is None:
        df['__RowText__'] = ''
        txt_col = '__RowText__'

    # Format date (fallback to original text if parsing fails)
    parsed = pd.to_datetime(df[date_col], errors='coerce', utc=True)
    date_out = parsed.dt.strftime('%Y-%m-%d').fillna(df[date_col].astype(str))

    # First three characters of Row Text for both Place and Position
    pos = df[txt_col].astype(str).str.strip().str[:3]

    shaped = pd.DataFrame({
        'Source':  '',            # Class blank
        'Regatta': df[reg_col],   # Regatta Name
        'Date':    date_out,      # Date
        'Place':   pos,           # First 3 chars of Row Text
        'Result':  pos            # First 3 chars of Row Text
    })

    # Return both list-of-dicts (for template) and DataFrame (for PDF)
    return shaped.to_dict(orient='records'), shaped

@app.route("/loading")
def loading_page():
    """Shows loading.html which connects to /stream-log via SSE."""
    return render_template("loading.html")

@app.route("/stream-log")
def stream_log():
    sailor_name = request.args.get("sailor_name", "").strip()
    start_date  = request.args.get("start_date", "").strip()
    end_date    = request.args.get("end_date", "").strip()
    max_regs    = request.args.get("max_regattas", "").strip()
    contains    = request.args.get("filter", "").strip()

    def sse(data, event=None):
        # helper to format SSE messages
        if event:
            return f"event: {event}\ndata: {data}\n\n"
        return f"data: {data}\n\n"

    def generate():
        # Build command for Resume.py
        cmd = [sys.executable, "-u", str(SCRAPER_SCRIPT), "--name", sailor_name]
        if start_date:
            cmd += ["--start_date", start_date]
        if end_date:
            cmd += ["--end_date", end_date]
        if max_regs:
            cmd += ["--max", max_regs]
        if contains:
            cmd += ["--contains", contains]

        # Show the exact command at the top of the stream
        yield sse("Starting Resume.py with:\n" + " ".join(cmd))

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # keep stdout unbuffered for SSE

        # Start process with stdout merged into stderr so we stream everything
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered
                env=env
            )
        except Exception as e:
            yield sse(f"Could not start scraper: {e}")
            yield sse("finished", event="done")
            return

        last_emit = time.time()

        # Stream lines; send SSE heartbeats during idle
        while True:
            line = process.stdout.readline()
            if not line:
                # heartbeat every 10s to keep proxies from closing the connection
                if time.time() - last_emit > 10:
                    yield ": keepalive\n\n"  # SSE comment (not buffered)
                    last_emit = time.time()
                if process.poll() is not None:
                    break
                time.sleep(0.25)
                continue

            yield sse(line.rstrip())
            last_emit = time.time()

        ret = process.wait()
        yield sse(f"Resume.py exited with code {ret}")
        yield sse("finished", event="done")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # ask proxy not to buffer
            "Connection": "keep-alive"
        }
    )

def csv_rows_for_template(csv_path: Path):
    """
    Read results.csv and return a list of dicts shaped for resume.html:
      - Source  -> "" (blank Class)
      - Regatta -> 'Regatta Name'
      - Date    -> 'Start Date (UTC)' formatted YYYY-MM-DD when possible
      - Place   -> first 3 chars of 'Row Text' (trimmed)
      - Result  -> first 3 chars of 'Row Text' (trimmed)
    """
    df = pd.read_csv(csv_path)
    reg_col  = 'Regatta' if 'Regatta' in df.columns else ('Regatta Name' if 'Regatta Name' in df.columns else None)
    date_col = 'Date' if 'Date' in df.columns else ('Start Date (UTC)' if 'Start Date (UTC)' in df.columns else None)
    txt_col  = 'Matched Row Text' if 'Matched Row Text' in df.columns else None
    if reg_col is None:
        df['__Regatta__'] = ''
        reg_col = '__Regatta__'
    if date_col is None:
        df['__Date__'] = ''
        date_col = '__Date__'
    if txt_col is None:
        df['__RowText__'] = ''
        txt_col = '__RowText__'
    parsed = pd.to_datetime(df[date_col], errors='coerce', utc=True)
    date_out = parsed.dt.strftime('%Y-%m-%d').fillna(df[date_col].astype(str))
    three = df[txt_col].astype(str).str.strip().str[:3]

    shaped = pd.DataFrame({
        'Source':  '',              # Class blank
        'Regatta': df[reg_col],     # Regatta Name
        'Date':    date_out,        # Date
        'Place':   three,           # First 3 chars of Row Text
        'Result':  three            # First 3 chars of Row Text
    })

    return shaped.to_dict(orient='records')

SCRAPER_DF_PATH = BASE_DIR / "scraper_df.csv"
@app.route("/completed")
def completed_page():
    sailor_name = (request.args.get("sailor_name") or "").strip()

    pdf_error = None

    # -----------------------
    # 1. SCRAPE TECHSCORE DATA
    # -----------------------
    scraper_df = None
    try:
        scraper_df = scrape_all_sites(sailor_name)
        if scraper_df is not None and not scraper_df.empty:
            scraper_df = expand_result_fields(scraper_df)
            scraper_df.to_csv(SCRAPER_DF_PATH, index=False)
    except Exception as e:
        pdf_error = f"Scraper step failed: {e}"
        scraper_df = None

    # -----------------------
    # 2. LOAD CLUBSPOT RESULTS
    # -----------------------
    results_df = None
    if RESULTS_CSV.exists():
        try:
            results_df = load_shaped_results(RESULTS_CSV)
        except Exception as e:
            pdf_error = f"Failed to load results.csv: {e}"

    # -----------------------
    # 3. BUILD ROWS (GLOBAL RowID SPACE)
    # -----------------------
    tech_rows = []
    club_rows = []
    offset = 0

    if scraper_df is not None and not scraper_df.empty:
        for i, row in scraper_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = i
            tech_rows.append(rec)
        offset = len(scraper_df)

    if results_df is not None and not results_df.empty:
        for j, row in results_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = offset + j
            club_rows.append(rec)

    # -----------------------
    # 4. LIST PRE-BUILT PDFs
    # -----------------------
    resumes = []
    try:
        if RESUMES_DIR.exists():
            for p in sorted(RESUMES_DIR.glob("*.pdf")):
                resumes.append({
                    "filename": p.name,
                    "name": p.stem.replace("_", " ").title()
                })
    except Exception:
        resumes = []

    # -----------------------
    # 5. RENDER (CLASSIC CONTRACT)
    # -----------------------
    return render_template(
        "resume.html",
        sailor_name=sailor_name.title() or "Sailor",
        techscore_rows=tech_rows,      # ✅ REQUIRED
        clubspot_rows=club_rows,       # ✅ REQUIRED
        pdf_available=False,
        pdf_error=pdf_error,
        results_available=bool(results_df is not None),
        results_url=url_for("download_results") if RESULTS_CSV.exists() else None,
        pdf_url=url_for("download_pdf", sailor_name=sailor_name) if RESULTS_CSV.exists() else None,
        csv_note=None,
        resumes=resumes
    )

@app.route("/apply-edits", methods=["POST"])
def apply_edits():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        return jsonify({"ok": False, "error": "Missing 'edits' list"}), 400

    # --- Load both datasets ---
    scraper_df = None
    if SCRAPER_DF_PATH.exists():
        try:
            scraper_df = pd.read_csv(SCRAPER_DF_PATH)
        except Exception:
            scraper_df = None

    results_df = load_shaped_results(RESULTS_CSV) if RESULTS_CSV.exists() else None

    EDITABLE = ["Source", "Regatta", "Date", "Place", "Result"]
    # Make editable cols object-typed so mixed string/number edits won’t warn/fail
    if scraper_df is not None:
        for c in EDITABLE:
            if c in scraper_df.columns:
                scraper_df[c] = scraper_df[c].astype("object")

    if results_df is not None:
        for c in EDITABLE:
            if c in results_df.columns:
                results_df[c] = results_df[c].astype("object")

    scraper_len = len(scraper_df) if scraper_df is not None else 0
    results_len = len(results_df) if results_df is not None else 0

    allowed = set(EDITABLE)

    for e in edits:
        try:
            r = int(e.get("row"))
        except Exception:
            return jsonify({"ok": False, "error": "Bad 'row' index"}), 400

        f = str(e.get("field"))
        v = e.get("value")

        if f not in allowed:
            return jsonify({"ok": False, "error": f"Field '{f}' not editable"}), 400

        if 0 <= r < scraper_len and scraper_df is not None:
            if f in scraper_df.columns:
                scraper_df.loc[r, f] = "" if v is None else v
            # else silently ignore if column missing
        elif scraper_len <= r < scraper_len + results_len and results_df is not None:
            results_index = r - scraper_len
            results_df.loc[results_index, f] = "" if v is None else v
        else:
            return jsonify({"ok": False, "error": f"Row {r} out of range"}), 400

    # --- Save back ---
    try:
        if results_df is not None:
            write_shaped_results(RESULTS_CSV, results_df)  # writes only Source..Result as designed
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to write results.csv: {e}"}), 500

    try:
        if scraper_df is not None:
            scraper_df.to_csv(SCRAPER_DF_PATH, index=False)
    except Exception:
        # best-effort: do not fail whole request
        pass

    return jsonify({"ok": True})

@app.route("/download-results")
def download_results():
    if RESULTS_CSV.exists():
        return send_file(str(RESULTS_CSV), as_attachment=True, download_name="results.csv")
    return "results.csv not found", 404

@app.route("/resume_modern")
def resume_modern_html():
    raw_name = (request.args.get("sailor_name")
                or request.args.get("name")
                or "").strip()
    display_name = raw_name.title() if raw_name else "Sailor"

    # ---- Techscore: scraper_df ----
    scraper_df = None
    if SCRAPER_DF_PATH.exists():
        try:
            scraper_df = pd.read_csv(SCRAPER_DF_PATH)
        except Exception:
            scraper_df = None

    # ---- Clubspot: shaped results_df ----
    results_df = None
    if RESULTS_CSV.exists():
        try:
            results_df = load_shaped_results(RESULTS_CSV)
        except Exception:
            results_df = None

    tech_rows = []
    club_rows = []
    offset = 0

    if scraper_df is not None and not scraper_df.empty:
        for i, row in scraper_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = i
            tech_rows.append(rec)
        offset = len(scraper_df)

    if results_df is not None and not results_df.empty:
        for j, row in results_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = offset + j
            club_rows.append(rec)

    return render_template(
        "resume_modern.html",
        sailor_name=display_name,
        techscore_rows=tech_rows,
        clubspot_rows=club_rows,
    )

@app.route("/resume_classic")
def resume_classic_html():
    teams_df = ensure_teams_csv(TEAMS_CSV)

    teams_rows = []
    for i, row in teams_df.iterrows():
        rec = row.to_dict()
        rec["RowID"] = i
        teams_rows.append(rec)

    # Accept ?sailor_name=... (normal) or ?name=... as a fallback
    raw_name = (request.args.get("sailor_name")
                or request.args.get("name")
                or "").strip()
    display_name = raw_name.title() if raw_name else "Sailor"

    # Techscore: scraper_df
    scraper_df = None
    if SCRAPER_DF_PATH.exists():
        try:
            scraper_df = pd.read_csv(SCRAPER_DF_PATH)
        except Exception:
            scraper_df = None

    # Clubspot: shaped results_df (Source, Regatta, Date, Place, Result)
    results_df = None
    if RESULTS_CSV.exists():
        try:
            results_df = load_shaped_results(RESULTS_CSV)
        except Exception:
            results_df = None

    tech_rows = []
    club_rows = []

    # Global RowID space must match /apply-edits logic:
    #   0..len(scraper_df)-1 -> Techscore
    #   len(scraper_df)..    -> Clubspot
    offset = 0

    if scraper_df is not None and not scraper_df.empty:
        for i, row in scraper_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = i
            tech_rows.append(rec)
        offset = len(scraper_df)

    if results_df is not None and not results_df.empty:
        for j, row in results_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = offset + j
            club_rows.append(rec)

    return render_template(
        "resume_classic.html",
        sailor_name=display_name,
        techscore_rows=tech_rows,
        clubspot_rows=club_rows,
        teams_rows=teams_rows,
    )

@app.route("/resume_minimalist")
def resume_minimalist_html():
    # Accept ?sailor_name=... or ?name=...
    raw_name = (request.args.get("sailor_name") or request.args.get("name") or "").strip()
    display_name = raw_name.title() if raw_name else "Sailor"

    # -----------------------
    # Techscore: scraper_df
    # -----------------------
    scraper_df = None
    if SCRAPER_DF_PATH.exists():
        try:
            scraper_df = pd.read_csv(SCRAPER_DF_PATH)
        except Exception as e:
            print(f"Failed to load scraper_df: {e}")
            scraper_df = None

    # -----------------------
    # Clubspot: results.csv (shaped)
    # -----------------------
    results_df = None
    if RESULTS_CSV.exists():
        try:
            results_df = load_shaped_results(RESULTS_CSV)
        except Exception as e:
            print(f"Failed to load results_df: {e}")
            results_df = None

    # -----------------------
    # Build rows (GLOBAL RowID SPACE)
    # -----------------------
    tech_rows = []
    club_rows = []
    offset = 0

    if scraper_df is not None and not scraper_df.empty:
        for i, row in scraper_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = i
            tech_rows.append(rec)
        offset = len(scraper_df)

    if results_df is not None and not results_df.empty:
        for j, row in results_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = offset + j
            club_rows.append(rec)

    # -----------------------
    # Render
    # -----------------------
    return render_template(
        "resume_minimalist.html",
        sailor_name=display_name,
        techscore_rows=tech_rows,     # ✅ REQUIRED
        clubspot_rows=club_rows,      # ✅ REQUIRED
    )

@app.route("/pdf_select")
def pdf_select():
    """Shows pdf_select.html which allows the user to pick a pdf style"""
    raw_name = (request.args.get("sailor_name") or "").strip()
    display_name = raw_name.title() if raw_name else "Sailor"

    return render_template(
        "pdf_select.html",
        sailor_name=display_name,
    )


# New: serve pre-built static resumes from static/resumes
@app.route("/download-static-resume/<path:filename>")
def download_static_resume(filename):
    '''# prevent path traversal by ensuring the resolved path is inside RESUMES_DIR
    try:
        requested = (RESUMES_DIR / filename).resolve()
        if not str(requested).startswith(str(RESUMES_DIR.resolve())):
            return "Invalid filename", 400
    except Exception:
        return "Invalid filename", 400
    file_path = RESUMES_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        return "File not found", 404
    return send_from_directory(str(RESUMES_DIR), filename, as_attachment=True)
    '''
    style = (request.args.get("style") or "classic").lower()

    filetoreturn = HTML(filename=f"{style}.html").write_pdf("resume.pdf")
    return filetoreturn
@app.route("/download_pdf")
def download_pdf():
    sailor_name = (request.args.get("sailor_name") or "").strip()

    # ---- Load / create teams.csv and build rows for template ----
    teams_df = ensure_teams_csv(TEAMS_CSV)
    teams_rows = []
    for ti, trow in teams_df.iterrows():
        rec = trow.to_dict()
        rec["RowID"] = int(ti)
        teams_rows.append(rec)

    # ---- Techscore: scraper_df ----
    scraper_df = None
    if SCRAPER_DF_PATH.exists():
        try:
            scraper_df = pd.read_csv(SCRAPER_DF_PATH)
        except Exception:
            scraper_df = None

    # ---- Clubspot: shaped results_df ----
    results_df = None
    if RESULTS_CSV.exists():
        try:
            results_df = load_shaped_results(RESULTS_CSV)
        except Exception:
            results_df = None

    tech_rows = []
    club_rows = []
    offset = 0

    if scraper_df is not None and not scraper_df.empty:
        for ri, row in scraper_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = int(ri)
            tech_rows.append(rec)
        offset = len(scraper_df)

    if results_df is not None and not results_df.empty:
        for rj, row in results_df.iterrows():
            rec = row.to_dict()
            rec["RowID"] = int(offset + rj)
            club_rows.append(rec)

    # ---- Render the HTML for the PDF ----
    rendered_html = render_template(
        "resume_classic.html",
        sailor_name=sailor_name.title() or "Sailor",
        teams_rows=teams_rows,          # IMPORTANT
        techscore_rows=tech_rows,
        clubspot_rows=club_rows,
        pdfrender=True,
    )

    # ---- Generate and send PDF ----
    pdf_file = PDF_PATH
    HTML(string=rendered_html).write_pdf(pdf_file)
    return send_file(pdf_file, as_attachment=True, download_name="resume.pdf")

def ensure_teams_csv(csv_path: Path, min_rows: int = 4) -> pd.DataFrame:
    cols = ["Team", "Years", "Role", "Notes"]

    # Create blank CSV if it doesn't exist
    if not csv_path.exists():
        df = pd.DataFrame([{c: "" for c in cols} for _ in range(min_rows)])
        df.to_csv(csv_path, index=False)
        return df

    df = pd.read_csv(csv_path)

    # Ensure columns exist
    for c in cols:
        if c not in df.columns:
            df[c] = ""

    # Ensure minimum number of rows
    if len(df) < min_rows:
        missing = min_rows - len(df)
        df = pd.concat(
            [df, pd.DataFrame([{c: "" for c in cols} for _ in range(missing)])],
            ignore_index=True
        )

    # 🔑 CRITICAL FIX: replace NaN with empty strings
    df = df.fillna("")

    # Persist normalized CSV
    df.to_csv(csv_path, index=False)

    return df[cols]



# ----- Sailing Teams edit route (THIS is the Flask route) -----
@app.route("/apply-team-edits", methods=["POST"])
def apply_team_edits():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        return jsonify({"ok": False, "error": "Missing 'edits' list"}), 400

    # IMPORTANT: define TEAMS_CSV near the top of app.py, e.g.:
    # TEAMS_CSV = BASE_DIR / "teams.csv"
    df = ensure_teams_csv(TEAMS_CSV)

    allowed = {"Team", "Years", "Role", "Notes"}

    for e in edits:
        try:
            r = int(e.get("row"))
        except Exception:
            return jsonify({"ok": False, "error": "Bad 'row' index"}), 400

        f = str(e.get("field"))
        v = e.get("value")

        if f not in allowed:
            return jsonify({"ok": False, "error": f"Field '{f}' not editable"}), 400
        if not (0 <= r < len(df)):
            return jsonify({"ok": False, "error": f"Row {r} out of range"}), 400

        df.loc[r, f] = "" if v is None else str(v)

    try:
        df.to_csv(TEAMS_CSV, index=False)
    except Exception as ex:
        return jsonify({"ok": False, "error": f"Failed to write teams.csv: {ex}"}), 500

    return jsonify({"ok": True})



if __name__ == "__main__":
    app.run(debug=True)