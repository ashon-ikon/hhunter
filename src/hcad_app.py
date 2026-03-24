"""
hcad_app.py — Flask dashboard for HCAD heat-maps.

Development:
    python -m src.hcad_app

Then open http://localhost:5050
"""
from __future__ import annotations

import io
import logging
import os
import statistics
from pathlib import Path

import requests as http_requests
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from src.hcad_maps import MAP_REGISTRY, OUT_DIR, generate_all
from src.hcad_screener import DEAL_TYPES, connect as screener_connect, deal_signals, screen

log = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent.parent / "templates"),
    static_folder=str(Path(__file__).parent.parent / "static"),
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def dashboard():
    maps = []
    for key, (fname, _) in MAP_REGISTRY.items():
        path = OUT_DIR / fname
        maps.append({
            "key":      key,
            "filename": fname,
            "exists":   path.exists(),
            "url":      url_for("serve_map", key=key),
        })
    return render_template("hcad_dashboard.html", maps=maps)


@app.route("/map/<key>")
def serve_map(key: str):
    if key not in MAP_REGISTRY:
        abort(404)
    fname, _ = MAP_REGISTRY[key]
    path = OUT_DIR / fname
    if not path.exists():
        # Auto-generate on first request
        log.info("Map %s not found — generating …", key)
        generate_all([key])
    return send_file(str(path.resolve()))


@app.route("/screener")
def screener():
    # Parse query params
    deal_type  = request.args.get("type", "flip")
    if deal_type not in DEAL_TYPES:
        deal_type = "flip"

    raw_zips   = request.args.get("zips", "").strip()
    zips       = [z.strip() for z in raw_zips.split(",") if z.strip()] or None
    min_score  = float(request.args.get("min_score", 40))
    min_price  = int(request.args.get("min_price")) if request.args.get("min_price") else None
    max_price  = int(request.args.get("max_price")) if request.args.get("max_price") else None
    max_year   = int(request.args.get("max_year"))  if request.args.get("max_year")  else None
    min_held   = int(request.args.get("min_held"))  if request.args.get("min_held")  else None
    limit      = int(request.args.get("limit", 50))
    fmt        = request.args.get("fmt", "html")

    results = None
    if "type" in request.args:   # only run query when form was submitted
        con = screener_connect()
        df  = screen(
            con, deal_type=deal_type, zips=zips, min_score=min_score,
            min_price=min_price, max_price=max_price,
            max_year_built=max_year, min_years_held=min_held, limit=limit,
        )
        con.close()

        # CSV export
        if fmt == "csv":
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            buf.seek(0)
            return send_file(
                io.BytesIO(buf.read().encode()),
                mimetype="text/csv",
                as_attachment=True,
                download_name=f"hcad-{deal_type}-screener.csv",
            )

        # Attach human-readable signals to each row
        results = []
        for _, row in df.iterrows():
            d = row.to_dict()
            d["signals"] = deal_signals(row, deal_type)
            results.append(d)

    score_col = DEAL_TYPES[deal_type]["score_col"]
    csv_url   = request.url.replace("fmt=html", "").rstrip("&?") + (
        ("&" if "?" in request.url else "?") + "fmt=csv"
    ) if results else "#"

    return render_template(
        "hcad_screener.html",
        deal_types=DEAL_TYPES,
        selected_type=deal_type,
        deal_meta={**DEAL_TYPES[deal_type], "score_col": score_col},
        selected_zips=raw_zips,
        min_score=min_score,
        min_price=min_price,
        max_price=max_price,
        max_year=max_year,
        min_held=min_held,
        limit=limit,
        results=results,
        csv_url=csv_url,
    )


@app.route("/api/zip-insight/<zip_code>")
def zip_insight(zip_code: str):
    """Fetch live HAR.com market data for a ZIP code and return JSON."""
    _HAR_URL = "https://www.har.com/api/SearchListings"
    try:
        r = http_requests.get(
            _HAR_URL,
            params={
                "zip_code":   zip_code,
                "for_sale":   1,
                "soldperiod": 1,
                "all_status": "A,OP,PS,P,closd",
                "sort":       "listdate desc",
                "view":       "map",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return jsonify({"zip": zip_code, "ok": False, "error": str(exc)})

    active = data.get("data", []) or []
    sold   = data.get("sold_data", []) or []

    def _med(lst):
        return round(statistics.median(lst)) if lst else None

    prices      = [l["LISTPRICE"]              for l in active if l.get("LISTPRICE")]
    ppsf_vals   = [float(l["PRICEPERSQFT"])    for l in active if l.get("PRICEPERSQFT")]
    dom_vals    = [int(l["DOM"])               for l in active if l.get("DOM")]
    sold_prices = [(l.get("SALESPRICE") or l.get("LISTPRICE"))
                   for l in sold if l.get("SALESPRICE") or l.get("LISTPRICE")]

    return jsonify({
        "zip":                zip_code,
        "ok":                 True,
        "active_count":       len(active),
        "total_active":       data.get("total", 0),
        "median_list_price":  _med(prices),
        "median_ppsf":        _med(ppsf_vals),
        "median_dom":         _med(dom_vals),
        "sold_count":         len(sold),
        "total_sold":         data.get("sold_total", 0),
        "median_sold_price":  _med(sold_prices),
    })


@app.route("/regen")
@app.route("/regen/<key>")
def regen(key: str | None = None):
    targets = [key] if key else None
    generate_all(targets)
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    port = int(os.environ.get("PORT", 5050))
    log.info("Starting HCAD dashboard on http://localhost:%d", port)
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
