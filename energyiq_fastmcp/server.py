import os
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from dateutil import parser as dtparse
from fastmcp import FastMCP
from urllib.parse import quote

# ---------- constants ----------
PKG_DIR = Path(__file__).resolve().parent
SITES_PATH = PKG_DIR / "sites.json"
TAGS_PATH = PKG_DIR / "tags.json"
ALGORITHM_PATH = PKG_DIR / "algorithm.txt"

DEFAULT_INTERVAL = "1h"
DEFAULT_SUMMARY = "Average"
FILTER_EXPR = "'.'<>\"No Data\" and '.' <>\"Bad\""

mcp = FastMCP("EnergyIQ (FastMCP)")


# ---------- helpers ----------
def _load_json(p: Path) -> Any:
    print(f"[INFO] Loading JSON from {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load {p}: {e}")
        raise

def _encode_uri_like_js(url: str) -> str:
    # Approx JS encodeURI: keep these characters unescaped
    safe = ":/?&=,+$#-_.!~*'()|\\"
    return quote(url, safe=safe)

def _utc_now_hour_floor() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(minute=0, second=0, microsecond=0)

def _ensure_iso_utc(s: Optional[str], default_dt: datetime) -> str:
    if not s:
        return default_dt.replace(microsecond=0).isoformat()
    dt = dtparse.isoparse(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

def _coerce_tags(tags_input, tags_map, site_code) -> List[str]:
    if not tags_input:
        print("[INFO] No tags passed, using all tags for site")
        return list(tags_map[site_code])
    if isinstance(tags_input, list):
        return [t.strip() for t in tags_input if t and t.strip()]
    if isinstance(tags_input, str):
        return [p.strip() for p in tags_input.split(",") if p.strip()]
    raise ValueError("tags must be a list or comma-separated string")


def _build_endpoint(
    base_url: str,
    from_iso: str,
    to_iso: str,
    device_id: int,
    interval: str,
    summary_type: str,
    tags: List[str]
) -> str:
    base = base_url if base_url.endswith("/") else base_url + "/"

    # Build raw string 
    qp = (
        f"?from_date={from_iso}"
        f"&to_date={to_iso}"
        f"&device_id={device_id}"
        f"&interval={interval}"
        f"&filterExpression={FILTER_EXPR}"
        f"&summaryType={summary_type}"
    )
    for t in tags:
        qp += f"&tag={t}"                  

    endpoint = _encode_uri_like_js(base + qp)
    print(f"[INFO] Built endpoint: {endpoint}")
    return endpoint

def _mock_series(from_iso: str, to_iso: str, step: timedelta, seed: float = 10.0):
    print(f"[INFO] Generating MOCK series from {from_iso} to {to_iso} step={step}")
    start = dtparse.isoparse(from_iso)
    end = dtparse.isoparse(to_iso)
    out = []
    i = 0
    cur = start
    while cur <= end:
        out.append({
            "Value": {
                "Timestamp": cur.replace(microsecond=0, tzinfo=timezone.utc).isoformat(),
                "Value": round(seed + i * 0.25, 3)
            }
        })
        cur += step
        i += 1
    print(f"[INFO] Generated {len(out)} MOCK points")
    return out


# ---------- tools ----------
@mcp.tool
def site_details(site_code: Optional[str] = None) -> Dict[str, Any]:
    """
    Return hard-coded site metadata from sites.json.
    If site_code is omitted, returns the first site.
    """
    print(f"[TOOL] site_details called with site_code={site_code}")
    sites = _load_json(SITES_PATH)

    if site_code:
        # Exact match first and second
        if site_code in sites:
            print(f"[INFO] Found exact match for site_code: {site_code}")
            return sites[site_code]
        
        # If no exact match, try partial matching or provide helpful error
        available_sites = list(sites.keys())
        print(f"[ERROR] Site code '{site_code}' not found. Available sites: {available_sites}")
        raise ValueError(f"Site code '{site_code}' not found. Available sites: {available_sites}")
    
    # No site_code provided, return first site
    key = next(iter(sites.keys()))
    print(f"[INFO] No site_code provided, defaulting to {key}")
    return sites[key]

@mcp.tool
def get_site_tags(site_code: Optional[str] = None) -> Dict[str, Any]:
    """
    Return list of tags for a site from tags.json.
    If site_code is omitted, returns tags for the first site.
    """
    print(f"[TOOL] get_site_tags called with site_code={site_code}")
    tags_map = _load_json(TAGS_PATH)
    if site_code and site_code in tags_map:
        return {"site_code": site_code, "tags": tags_map[site_code]}
    key = next(iter(tags_map.keys()))
    print(f"[INFO] No site_code provided, defaulting to {key}")
    return {"site_code": key, "tags": tags_map[key]}

@mcp.tool
async def get_data(
    site_code: Optional[str] = None,
    tag: Optional[str] = None,
    from_iso: Optional[str] = None,
    to_iso: Optional[str] = None,
    interval: Optional[str] = None,
    summary_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch site-tag data. Uses real API if env is set, else returns mocked data.
    Returns: { "site_code": ..., "tag": ..., "items": [ { "Value": { "Timestamp": "...Z", "Value": n } } ] }
    """
    print(f"[TOOL] get_data called with site={site_code}, tag={tag}, from={from_iso}, to={to_iso}")
    sites = _load_json(SITES_PATH)
    tags_map = _load_json(TAGS_PATH)

    site_code = site_code or next(iter(sites.keys()))
    tag = tag or tags_map[site_code][0]
    device_id = int(sites[site_code]["device_id"])
    print(f"[INFO] Resolved site={site_code}, tag={tag}, device_id={device_id}")

    now_floor = _utc_now_hour_floor()
    from_iso = _ensure_iso_utc(from_iso, now_floor - timedelta(hours=1))
    to_iso   = _ensure_iso_utc(to_iso, now_floor)
    interval = interval or DEFAULT_INTERVAL
    summary_type = summary_type or DEFAULT_SUMMARY
    print(f"[INFO] Window: {from_iso} -> {to_iso}, interval={interval}, summary={summary_type}")

    base_url = os.getenv("BASE_URL", "").strip()
    client_id = os.getenv("CLIENT_ID", "").strip()
    client_secret = os.getenv("CLIENT_SECRET", "").strip()

    if base_url and client_id and client_secret:
        endpoint = _build_endpoint(base_url, from_iso, to_iso, device_id, interval, summary_type, [tag])
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    endpoint,
                    headers={
                        "Client-Id": client_id,
                        "Client-Secret": client_secret,
                        "Accept": "application/json"
                    }
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            print(f"[ERROR] API request failed: {e}")
            raise

        if not isinstance(data, dict) or not data.get("success") or not isinstance(data.get("items"), list):
            print("[ERROR] API returned unexpected payload")
            raise RuntimeError(f"Unexpected API payload: {str(data)[:400]}")
        print(f"[INFO] API call successful, items={len(data['items'])}")
        return {"site_code": site_code, "tag": tag, "items": data["items"]}

    # mock path
    step = timedelta(hours=1) if interval.endswith("h") else timedelta(minutes=15)
    items = [{"Value": x["Value"]} for x in _mock_series(from_iso, to_iso, step)]
    print(f"[INFO] Returning MOCK data, items={len(items)}")
    return {"site_code": site_code, "tag": tag, "items": items}


@mcp.tool
def evaluate_algorithm(
    lmp: float,
    threshold: float,
    ecomax: float,
    unit_status: str,
    dispatch: float,
    dispatch_max: float,
    mw: float,
    timer: float = 0.0,
    threshold_flag: bool = True,
    site_name: str = "Unknown Site"
) -> Dict[str, Any]:
    """
    Evaluate the curtailment decision algorithm based on the algorithm.txt specification.
    
    Args:
        lmp: Locational Marginal Price (market price for electricity)
        threshold: LMP threshold price the site is willing to accept
        ecomax: Maximum allowable output
        unit_status: Whether the unit is "ON" or "OFF"
        dispatch: Current dispatch order
        dispatch_max: Maximum dispatch value
        mw: Current generation output (Megawatt)
        timer: Time in minutes that conditions have been met (default: 0.0)
        threshold_flag: Whether to use LMP/threshold comparison (default: True)
        site_name: Name of the site being evaluated (default: "Unknown Site")
    
    Returns:
        Dictionary containing curtailment decision, dispatch value, and evaluation log
    """
    print(f"[TOOL] evaluate_algorithm called for site={site_name}")
    
    # Read algorithm specification
    try:
        with ALGORITHM_PATH.open("r", encoding="utf-8") as f:
            algorithm_text = f.read()
    except Exception as e:
        return {
            "error": f"Failed to read algorithm specification: {e}",
            "curtailment_command": 0,
            "dispatch_value": dispatch,
            "timer": timer
        }
    
    # Initialize result
    result = {
        "site_name": site_name,
        "inputs": {
            "lmp": lmp,
            "threshold": threshold,
            "ecomax": ecomax,
            "unit_status": unit_status,
            "dispatch": dispatch,
            "dispatch_max": dispatch_max,
            "mw": mw,
            "timer": timer,
            "threshold_flag": threshold_flag
        },
        "curtailment_command": 0,
        "dispatch_value": dispatch,
        "timer": timer,
        "evaluation_log": [],
        "algorithm_reference": "Based on algorithm.txt curtailment decision specification"
    }
    
    log = result["evaluation_log"]
    
    # Step 1: Check Site Output
    log.append(f"Step 1: Checking site output - EcoMax: {ecomax}, MW: {mw}")
    
    if ecomax <= 0.5 and mw <= 0.5:
        log.append("Both EcoMax and MW are ≤ 0.5 - no curtailment needed")
        result["curtailment_command"] = 0
        result["dispatch_value"] = dispatch
        log.append(f"Decision: No curtailment, dispatch remains {dispatch}")
        return result
    
    log.append("Site output sufficient for curtailment evaluation - proceeding to curtailment logic")
    
    # Step 2: Curtailment Logic Path
    if threshold_flag:
        log.append(f"Step 2: Using LMP/Threshold comparison - LMP: {lmp}, Threshold: {threshold}")
        
        if lmp <= threshold:
            log.append(f"LMP ({lmp}) ≤ Threshold ({threshold}) - curtailment triggered")
            result["curtailment_command"] = 1
            
            if unit_status.upper() == "ON":
                log.append("Unit is ON - curtailing and keeping current dispatch")
                result["dispatch_value"] = dispatch
            else:
                log.append("Unit is OFF - curtailing and setting dispatch to zero")
                result["dispatch_value"] = 0
                
        else:
            log.append(f"LMP ({lmp}) > Threshold ({threshold}) - using EcoMax logic")
            result = _evaluate_ecomax_logic(result, log, ecomax, dispatch, dispatch_max, unit_status, timer)
    else:
        log.append("Step 2: Threshold flag is OFF - using EcoMax logic only")
        result = _evaluate_ecomax_logic(result, log, ecomax, dispatch, dispatch_max, unit_status, timer)
    
    log.append(f"Final Decision: Curtailment={result['curtailment_command']}, Dispatch={result['dispatch_value']}")
    return result


def _evaluate_ecomax_logic(result, log, ecomax, dispatch, dispatch_max, unit_status, timer):
    """Helper function to evaluate EcoMax logic"""
    
    if unit_status.upper() == "ON":
        log.append(f"Unit is ON - checking EcoMax logic: EcoMax={ecomax}, Dispatch={dispatch}")
        ecomax_dispatch_diff = ecomax - dispatch
        log.append(f"EcoMax - Dispatch = {ecomax_dispatch_diff}")
        
        if ecomax_dispatch_diff > 0.2:
            log.append(f"Difference > 0.2 - checking timer: {timer} minutes")
            if timer > 2.5:
                log.append("Timer > 2.5 minutes - curtailing and keeping current dispatch")
                result["curtailment_command"] = 1
                result["dispatch_value"] = dispatch
            else:
                log.append("Timer ≤ 2.5 minutes - no curtailment, setting dispatch to max")
                result["curtailment_command"] = 0
                result["dispatch_value"] = dispatch_max
        else:
            log.append("EcoMax - Dispatch ≤ 0.2 - no curtailment, setting dispatch to max")
            result["curtailment_command"] = 0
            result["dispatch_value"] = dispatch_max
    else:
        log.append("Unit is OFF - no curtailment, setting dispatch to max")
        result["curtailment_command"] = 0
        result["dispatch_value"] = dispatch_max
    
    return result


def main():
    print("[INFO] Starting MCP server...")
    mcp.run("http")


if __name__ == "__main__":
    main()
