#!/usr/bin/env python3
"""Automated macOS Network Troubleshooter MCP Server.

A zero-configuration Model Context Protocol (MCP) server that empowers LLMs to
diagnose and resolve macOS Wi-Fi and network issues using native system tools.

Employs the 'Trojan Horse' docstring pattern and 'LLM Directive' response pattern
to force the LLM into a friendly, patient, jargon-free IT technician persona.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    # mcp 1.x
    from mcp.server.fastmcp import FastMCP
except ImportError:
    try:
        # mcp 2.x
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        # Graceful fallback stub when mcp is not yet installed in the environment
        class FastMCP:  # type: ignore
            def __init__(self, name: str):
                self.name = name

            def tool(self):
                def decorator(func):
                    return func
                return decorator

            def run(self, *args, **kwargs):
                print(f"FastMCP '{self.name}' initialized. Run 'pip install -r requirements.txt' to enable full MCP server.")

# Initialize FastMCP server
mcp = FastMCP("macos-wifi-troubleshooter")

# Standard paths on macOS
AIRPORT_PATH = (
    "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
)
NETTOP_PATH = "/usr/bin/nettop"
NETWORKQUALITY_PATH = "/usr/bin/networkquality"
ROUTE_PATH = "/sbin/route"
PING_PATH = "/sbin/ping"
DIG_PATH = "/usr/bin/dig"

# Hotspot Fallback Awareness constant
HOTSPOT_GATEWAY = "172.20.10.1"
HOTSPOT_DIRECTIVE = (
    "The user is connected to a cellular hotspot backup. Remind the user that these "
    "speed/ping metrics reflect their cellular connection, not their home ISP. "
    "Evaluate the home router strictly based on the environmental Wi-Fi scan."
)


# ==============================================================================
# Native Subprocess and Network Helpers
# ==============================================================================

def run_command(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str, Optional[str]]:
    """Executes a native macOS command safely with timeout and error handling.
    
    Returns:
        (exit_code, stdout, stderr, error_message)
    """
    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return process.returncode, process.stdout, process.stderr, None
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout} seconds", f"Timeout ({timeout}s)"
    except FileNotFoundError as exc:
        return -1, "", str(exc), f"Command not found: {cmd[0]}"
    except PermissionError as exc:
        return -1, "", str(exc), f"Permission denied: {cmd[0]}"
    except Exception as exc:
        return -1, "", str(exc), f"Execution error: {str(exc)}"


def get_default_gateway() -> Optional[str]:
    """Finds the local default gateway IP using `route -n get default`."""
    code, stdout, _, _ = run_command([ROUTE_PATH, "-n", "get", "default"], timeout=5)
    if code == 0 and stdout:
        match = re.search(r"gateway:\s*([0-9.]+)", stdout)
        if match:
            return match.group(1).strip()
    return None


def get_airport_binary() -> Optional[str]:
    """Finds the airport CLI binary or returns None."""
    if os.path.exists(AIRPORT_PATH) and os.access(AIRPORT_PATH, os.X_OK):
        return AIRPORT_PATH
    which_path = shutil.which("airport")
    if which_path and os.access(which_path, os.X_OK):
        return which_path
    return None


def get_current_wifi_details() -> Dict[str, Any]:
    """Retrieves current Wi-Fi status using airport -I with fallbacks."""
    airport_bin = get_airport_binary()
    if airport_bin:
        code, stdout, stderr, err = run_command([airport_bin, "-I"], timeout=8)
        if code == 0 and stdout:
            parsed = {}
            for line in stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    parsed[k.strip()] = v.strip()
            return parsed
    
    # Fallback to networksetup for SSID if airport is missing (e.g. newer macOS)
    code, stdout, _, _ = run_command(["networksetup", "-getairportnetwork", "en0"], timeout=5)
    if code == 0 and "Current Wi-Fi Network:" in stdout:
        ssid = stdout.split("Current Wi-Fi Network:", 1)[1].strip()
        return {"SSID": ssid, "state": "running"}
    
    return {}


def is_hotspot_active(gateway_ip: Optional[str] = None, ssid: Optional[str] = None) -> bool:
    """Checks whether the user is connected through an Apple iOS Personal Hotspot.
    
    Apple iOS Personal Hotspot always assigns the 172.20.10.1 gateway IP
    or includes 'iPhone' in the broadcasted Wi-Fi SSID.
    """
    if gateway_ip == HOTSPOT_GATEWAY:
        return True
    if ssid and "iphone" in ssid.lower():
        return True
    
    current_gw = gateway_ip or get_default_gateway()
    if current_gw == HOTSPOT_GATEWAY:
        return True
    
    wifi_details = get_current_wifi_details()
    cur_ssid = ssid or wifi_details.get("SSID", "")
    if cur_ssid and "iphone" in cur_ssid.lower():
        return True
    
    return False


def format_bytes(byte_count: int) -> str:
    """Converts raw byte count into human-friendly string (e.g., 14.50 MB)."""
    count = float(byte_count)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(count) < 1024.0:
            return f"{count:.2f} {unit}"
        count /= 1024.0
    return f"{count:.2f} PB"


# ==============================================================================
# Tool 1: check_local_bandwidth_hogs
# ==============================================================================

@mcp.tool()
def check_local_bandwidth_hogs() -> str:
    """Inspects running macOS processes and measures per-app network bandwidth consumption.

    Executes `nettop` in snapshot logging mode to capture active data transfers, sums total
    bytes in/out per process, and identifies the top 5 network-consuming applications on the Mac.

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
      Speak with warmth and empathy to a user who has ZERO network knowledge.
    - NO JARGON: Never say 'TCP streams', 'packet sockets', 'I/O buffers', or 'process PIDs'.
      Describe apps as 'taking up internet lanes' or 'downloading in the background'.
    - DIRECTIVE ADHERENCE: Inspect the '_llm_directive' key in the output JSON. It tells you
      the exact condition discovered and what to say. If a specific app is hogging bandwidth,
      tell the user: "The app [App Name] is downloading or uploading a massive amount of data
      in the background. Try closing it or pausing its updates to speed up your connection."
    - USER ACTION: If an app is hogging data, give simple steps to quit or pause it.
    """
    cmd = [NETTOP_PATH, "-P", "-L", "1", "-k", "state,interface", "-J", "bytes_in,bytes_out"]
    code, stdout, stderr, err = run_command(cmd, timeout=12)

    raw_data: Dict[str, Any] = {}
    directive_parts: List[str] = []

    # Check for iOS Hotspot
    if is_hotspot_active():
        directive_parts.append(HOTSPOT_DIRECTIVE)

    if code != 0 or not stdout.strip():
        raw_data = {
            "error": err or stderr.strip() or "nettop execution failed",
            "exit_code": code,
            "top_apps": []
        }
        directive_parts.append(
            "Explain to the user that macOS could not read live per-app network usage due to "
            "security permissions. Assure them this is normal, and gently guide them to open "
            "Activity Monitor (press Command + Space, type 'Activity Monitor', click the Network "
            "tab) to check if an app like Dropbox, Steam, or iCloud is currently downloading."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Parse nettop output
    process_traffic: Dict[str, Dict[str, int]] = {}
    reader = csv.reader(io.StringIO(stdout))
    
    for row in reader:
        if not row or len(row) < 3:
            continue
        
        # Check if the last two fields are integer numbers
        try:
            b_in = int(row[-2].strip())
            b_out = int(row[-1].strip())
        except (ValueError, IndexError):
            continue  # Header or non-data row
        
        # Process name is typically right before the numbers (or the first element)
        raw_proc = row[-3].strip() if len(row) >= 3 else row[0].strip()
        # Clean process name by removing the trailing .PID
        proc_name = re.sub(r"\.\d+$", "", raw_proc)
        if not proc_name or proc_name.lower() in ("bytes_in", "process", "time"):
            continue
        
        if proc_name not in process_traffic:
            process_traffic[proc_name] = {"bytes_in": 0, "bytes_out": 0, "total_bytes": 0}
        
        process_traffic[proc_name]["bytes_in"] += b_in
        process_traffic[proc_name]["bytes_out"] += b_out
        process_traffic[proc_name]["total_bytes"] += (b_in + b_out)

    # Sort processes by total bytes descending
    sorted_apps = sorted(
        process_traffic.items(),
        key=lambda item: item[1]["total_bytes"],
        reverse=True
    )

    top_5 = []
    total_tracked_bytes = sum(item[1]["total_bytes"] for item in sorted_apps)

    for name, stats in sorted_apps[:5]:
        top_5.append({
            "app_name": name,
            "bytes_in": stats["bytes_in"],
            "bytes_out": stats["bytes_out"],
            "total_bytes": stats["total_bytes"],
            "human_readable": format_bytes(stats["total_bytes"]),
        })

    raw_data = {
        "top_bandwidth_apps": top_5,
        "total_bandwidth_tracked": format_bytes(total_tracked_bytes),
        "total_bytes_tracked": total_tracked_bytes,
    }

    # Evaluate if a single process is consuming massive bandwidth
    # Threshold: >= 25MB total or >= 10MB while representing > 60% of all traffic
    hog_detected = False
    if top_5:
        top_app = top_5[0]
        top_bytes = top_app["total_bytes"]
        is_large = top_bytes >= (25 * 1024 * 1024)
        is_dominant = (total_tracked_bytes > 0) and (top_bytes / total_tracked_bytes >= 0.60) and (top_bytes >= 10 * 1024 * 1024)

        if is_large or is_dominant:
            hog_detected = True
            directive_parts.append(
                f"The app {top_app['app_name']} is downloading or uploading a massive amount "
                "of data in the background. Try closing it or pausing its updates to speed up "
                "your connection."
            )

    if not hog_detected:
        directive_parts.append(
            "Inform the user that none of the applications on their Mac are hogging bandwidth. "
            "Local network activity is peaceful and normal across all apps. Reassure them that "
            "background applications are not causing their connection slowdown."
        )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Tool 2: get_wifi_metrics
# ==============================================================================

@mcp.tool()
def get_wifi_metrics() -> str:
    """Reads current Wi-Fi physical link metrics (RSSI, Noise, Tx Rate, Channel, and Band).

    Queries macOS internal 802.11 Wi-Fi subsystem using the native `airport` utility.
    Extracts signal strength (RSSI), background noise, transmission speed, and frequency band.

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
      Speak warmly to a non-technical user.
    - NO JARGON: Do NOT use acronyms like 'RSSI', 'dBm', 'SNR', or 'MCS index'.
      Translate them into simple concepts like 'signal strength', 'radio interference', and 'connection speed'.
    - DIRECTIVE ADHERENCE: Inspect '_llm_directive' closely.
      - If Hotspot is active, follow the cellular reminder directive.
      - If signal is worse than -70 dBm, the directive MUST state:
        "Tell the user their Wi-Fi signal is getting physically blocked. Give them two simple
        choices: move their Mac into the same room as the Wi-Fi router, or connect to the 2.4GHz
        Wi-Fi network instead, which travels through walls much better."
      - If signal is healthy, reassure them that the wireless link between Mac and router is strong.
    """
    airport_bin = get_airport_binary()
    raw_data: Dict[str, Any] = {}
    directive_parts: List[str] = []

    # Fallback if airport is completely unavailable
    if not airport_bin:
        wifi_info = get_current_wifi_details()
        ssid = wifi_info.get("SSID", "")
        gateway = get_default_gateway()
        hotspot = is_hotspot_active(gateway, ssid)
        
        if hotspot:
            directive_parts.append(HOTSPOT_DIRECTIVE)

        raw_data = {
            "error": "The airport command utility is not accessible on this macOS version",
            "ssid": ssid,
            "connected": bool(ssid)
        }
        directive_parts.append(
            "Inform the user that their current connected network is '" + (ssid or "unknown") + "'. "
            "Because deep Wi-Fi radio stats are restricted by their macOS version, advise them "
            "to check their menu bar Wi-Fi icon (Option + click the Wi-Fi symbol) to quickly see "
            "their signal bars and transfer rate."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    code, stdout, stderr, err = run_command([airport_bin, "-I"], timeout=10)

    if code != 0 or not stdout.strip():
        raw_data = {"error": err or stderr.strip() or "Failed to read airport metrics"}
        directive_parts.append(
            "Explain to the user that their Mac cannot read Wi-Fi details right now. "
            "Ask them to ensure Wi-Fi is turned on in the top-right menu bar and connected "
            "to their home network."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Parse key-value airport output
    metrics: Dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            metrics[k.strip()] = v.strip()

    state = metrics.get("state", "unknown")
    ssid = metrics.get("SSID", "")
    bssid = metrics.get("BSSID", "")

    # Parse numeric indicators
    try:
        rssi = int(metrics.get("agrCtlRSSI", metrics.get("RSSI", "-100")))
    except ValueError:
        rssi = -100

    try:
        noise = int(metrics.get("agrCtlNoise", metrics.get("Noise", "-90")))
    except ValueError:
        noise = -90

    try:
        tx_rate = float(metrics.get("lastTxRate", "0"))
    except ValueError:
        tx_rate = 0.0

    channel_raw = metrics.get("channel", "unknown")
    channel_match = re.match(r"(\d+)", channel_raw)
    primary_channel = int(channel_match.group(1)) if channel_match else 0

    # Determine band
    if primary_channel <= 14:
        band = "2.4 GHz"
    elif 36 <= primary_channel <= 177:
        band = "5 GHz"
    elif primary_channel > 177:
        band = "6 GHz (Wi-Fi 6E/7)"
    else:
        band = "Unknown"

    snr = rssi - noise

    raw_data = {
        "ssid": ssid,
        "bssid": bssid,
        "state": state,
        "rssi_dbm": rssi,
        "noise_dbm": noise,
        "snr_db": snr,
        "tx_rate_mbps": tx_rate,
        "channel": channel_raw,
        "primary_channel": primary_channel,
        "band": band,
    }

    # Evaluate Hotspot Fallback Awareness
    gateway = get_default_gateway()
    if is_hotspot_active(gateway, ssid):
        directive_parts.append(HOTSPOT_DIRECTIVE)
    else:
        # Check signal threshold: worse than -70 dBm (e.g. -75, -80 dBm)
        if rssi < -70:
            directive_parts.append(
                "Tell the user their Wi-Fi signal is getting physically blocked. Give them two "
                "simple choices: move their Mac into the same room as the Wi-Fi router, or "
                "connect to the 2.4GHz Wi-Fi network instead, which travels through walls much better."
            )
        elif state.lower() != "running" or not ssid:
            directive_parts.append(
                "Tell the user their Mac appears to be disconnected from Wi-Fi. Guide them "
                "to click the Wi-Fi symbol at the top right of their screen and select their network."
            )
        else:
            directive_parts.append(
                f"Inform the user that their Wi-Fi signal strength is strong and healthy ({rssi} dBm "
                f"on {band}). The physical wireless connection between their Mac and the router "
                "is in great shape with no physical blockage."
            )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Tool 3: scan_wifi_congestion
# ==============================================================================

@mcp.tool()
def scan_wifi_congestion() -> str:
    """Scans neighboring Wi-Fi networks to identify frequency channel congestion.

    Executes `airport -s` to discover all broadcasting routers in range, parses their BSSIDs,
    signal strengths (RSSI), and operational channels, and identifies crowded vs. optimal channels.

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
    - NO JARGON: Do not talk about 'co-channel interference', 'adjacent channel bleeding',
      or '802.11 spectrum overlapping'. Use the analogy of 'too many people shouting in the same room'.
    - DIRECTIVE ADHERENCE: Inspect '_llm_directive'. If neighbors are overlapping on their channel,
      instruct the LLM:
      "Explain to the user that their neighbors' Wi-Fi routers are 'shouting over' theirs on the
      same frequency. Tell them to open their router's app on their phone and change the Wi-Fi
      channel to [Optimal Channel] to get a clear lane."
    - LOCATION PERMISSIONS: If macOS blocks Wi-Fi scanning due to Location Services, provide simple,
      numbered steps to enable Location Services for Terminal in macOS System Settings.
    """
    airport_bin = get_airport_binary()
    directive_parts: List[str] = []

    if not airport_bin:
        raw_data = {"error": "The airport utility is unavailable on this macOS configuration."}
        directive_parts.append(
            "Explain to the user that macOS does not have the wireless scanning tool enabled. "
            "Advise them that if they feel their Wi-Fi is crowded, they can open their router's "
            "mobile app and look for an 'Auto Channel' or 'Channel Optimization' button."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    code, stdout, stderr, err = run_command([airport_bin, "-s"], timeout=15)

    # Check for Location Services denial (common macOS security block)
    loc_denied = (
        "location" in (stderr + stdout).lower()
        or "permission" in (stderr + stdout).lower()
        or "not permitted" in (stderr + stdout).lower()
        or (code != 0 and not stdout.strip())
    )

    if loc_denied:
        raw_data = {
            "error": "Location Services permission denied or unavailable",
            "stderr": stderr.strip(),
            "exit_code": code,
        }
        directive_parts.append(
            "Explain to the user that macOS requires Location Services permission to scan nearby "
            "Wi-Fi networks. Give them simple, numbered steps: Open System Settings > Privacy & "
            "Security > Location Services, ensure Location Services is turned On, and ensure "
            "Terminal (or their current app) is allowed."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Parse neighboring networks
    # Format typically: SSID BSSID RSSI CHANNEL HT CC SECURITY...
    networks = []
    channel_counts: Dict[int, int] = {}

    pattern = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s+([-\d]+)\s+([0-9,\+-]+)")

    for line in stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        
        bssid = match.group(1)
        rssi = int(match.group(2))
        chan_str = match.group(3)
        ssid = line[:match.start()].strip()
        
        # Primary channel number
        primary_match = re.match(r"(\d+)", chan_str)
        if not primary_match:
            continue
        primary_ch = int(primary_match.group(1))
        
        channel_counts[primary_ch] = channel_counts.get(primary_ch, 0) + 1
        networks.append({
            "ssid": ssid or "<Hidden SSID>",
            "bssid": bssid,
            "rssi": rssi,
            "channel": primary_ch,
        })

    # Determine congestion and optimal channels
    # 2.4 GHz non-overlapping channels: 1, 6, 11
    counts_2g = {ch: channel_counts.get(ch, 0) for ch in [1, 6, 11]}
    best_2g_channel = min(counts_2g, key=counts_2g.get)

    # 5 GHz clean channels: 36, 40, 44, 48, 149, 153, 157, 161
    popular_5g = [36, 40, 44, 48, 149, 153, 157, 161]
    counts_5g = {ch: channel_counts.get(ch, 0) for ch in popular_5g}
    best_5g_channel = min(counts_5g, key=counts_5g.get)

    most_congested_channel = max(channel_counts, key=channel_counts.get) if channel_counts else None
    max_congestion = channel_counts.get(most_congested_channel, 0) if most_congested_channel else 0

    optimal_channel_desc = f"Channel {best_2g_channel} (for 2.4GHz) or Channel {best_5g_channel} (for 5GHz)"

    raw_data = {
        "networks_detected": len(networks),
        "most_congested_channel": most_congested_channel,
        "max_overlapping_networks_on_single_channel": max_congestion,
        "recommended_2g_channel": best_2g_channel,
        "recommended_5g_channel": best_5g_channel,
        "optimal_channel_recommendation": optimal_channel_desc,
        "channel_distribution": channel_counts,
        "scanned_networks": networks[:15],
    }

    if max_congestion >= 2:
        directive_parts.append(
            f"Explain to the user that their neighbors' Wi-Fi routers are 'shouting over' theirs "
            f"on the same frequency. Tell them to open their router's app on their phone and change "
            f"the Wi-Fi channel to {optimal_channel_desc} to get a clear lane."
        )
    else:
        directive_parts.append(
            "Inform the user that the wireless airwaves around them are very peaceful. Very few "
            "neighbors are broadcasting nearby, so wireless congestion is not their problem."
        )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Tool 4: measure_internet_quality
# ==============================================================================

@mcp.tool()
def measure_internet_quality() -> str:
    """Measures internet speed (download/upload) and connection responsiveness (bufferbloat/RPM).

    Executes macOS native `networkquality -c` utility to measure real-world throughput
    and network responsiveness under working load (Round-trips Per Minute - RPM).

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
    - NO JARGON: Never say 'bufferbloat', 'TCP queue congestion', 'L4S', or 'asymmetric bandwidth'.
      Describe responsiveness as a 'traffic jam' on a highway.
    - DIRECTIVE ADHERENCE: Inspect '_llm_directive'.
      - If Hotspot is active, follow the cellular reminder directive.
      - If RPM is low, instruct the LLM:
        "Explain that their router is experiencing a 'traffic jam' (bufferbloat). A large download
        is clogging the connection, which is why real-time apps like Zoom are stuttering or freezing."
      - If RPM is high, reassure them that their connection handles heavy loads smoothly.
    """
    cmd = [NETWORKQUALITY_PATH, "-c"]
    code, stdout, stderr, err = run_command(cmd, timeout=45)

    raw_data: Dict[str, Any] = {}
    directive_parts: List[str] = []

    # Check for iOS Hotspot
    if is_hotspot_active():
        directive_parts.append(HOTSPOT_DIRECTIVE)

    if code != 0 or not stdout.strip():
        raw_data = {
            "error": err or stderr.strip() or "networkquality failed to run",
            "exit_code": code,
        }
        directive_parts.append(
            "Tell the user that the internet speed and responsiveness test could not connect to "
            "Apple's measurement servers. Their Mac appears to be having trouble reaching the wider "
            "internet right now."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raw_data = {"raw_output": stdout, "error": "Unable to parse JSON output"}
        directive_parts.append(
            "Explain to the user that the speed test completed but returned an unexpected format. "
            "Reassure them and suggest running ping diagnostics to test connectivity."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Convert bps to Mbps
    dl_bps = data.get("dl_throughput", 0)
    ul_bps = data.get("ul_throughput", 0)
    dl_mbps = round(dl_bps / 1_000_000, 2)
    ul_mbps = round(ul_bps / 1_000_000, 2)
    rpm = data.get("responsiveness", 0)
    base_rtt = data.get("base_rtt", 0)

    raw_data = {
        "download_speed_mbps": dl_mbps,
        "upload_speed_mbps": ul_mbps,
        "responsiveness_rpm": rpm,
        "base_latency_ms": base_rtt,
        "raw_metrics": data,
    }

    # Evaluate Responsiveness (Apple classifies Low as < 400 RPM)
    if not is_hotspot_active():
        if rpm < 400:
            directive_parts.append(
                "Explain that their router is experiencing a 'traffic jam' (bufferbloat). "
                "A large download is clogging the connection, which is why real-time apps like "
                "Zoom are stuttering or freezing."
            )
        else:
            directive_parts.append(
                f"Inform the user that their internet quality is excellent and responsive ({rpm} RPM). "
                f"With download speeds of {dl_mbps} Mbps and upload speeds of {ul_mbps} Mbps, "
                "their connection can handle video calls, streaming, and gaming without stuttering."
            )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Tool 5: ping_diagnostics
# ==============================================================================

def _parse_ping_output(ping_stdout: str) -> Dict[str, Any]:
    """Parses packet loss and min/avg/max latency from standard macOS ping output."""
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", ping_stdout)
    packet_loss = float(loss_match.group(1)) if loss_match else 100.0

    rtt_match = re.search(
        r"(?:round-trip|rtt)\s+min/avg/max/(?:stddev|mdev)\s*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms",
        ping_stdout,
    )
    if rtt_match:
        min_ms = float(rtt_match.group(1))
        avg_ms = float(rtt_match.group(2))
        max_ms = float(rtt_match.group(3))
        stddev_ms = float(rtt_match.group(4))
    else:
        min_ms = avg_ms = max_ms = stddev_ms = None

    return {
        "packet_loss_percent": packet_loss,
        "min_latency_ms": min_ms,
        "avg_latency_ms": avg_ms,
        "max_latency_ms": max_ms,
        "stddev_latency_ms": stddev_ms,
    }


@mcp.tool()
def ping_diagnostics() -> str:
    """Diagnoses connection health by pinging both the local Wi-Fi gateway and the public internet.

    Finds the local gateway IP using `route -n get default`, executes a 5-packet ping to both
    the local Gateway and public DNS (8.8.8.8), and calculates latency and packet loss.

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
    - NO JARGON: Do NOT use terms like 'ICMP echo', 'TTL expiration', 'default gateway', or 'packet drop'.
      Refer to the gateway simply as 'your home Wi-Fi router' and 8.8.8.8 as 'the outside internet'.
    - DIRECTIVE ADHERENCE: Inspect '_llm_directive'.
      - If gateway is 172.20.10.1, apply Hotspot logic.
      - Otherwise, if ping to Gateway is <5ms but ping to 8.8.8.8 drops packets or exceeds 100ms,
        the directive MUST state:
        "Tell the user their home Wi-Fi is working perfectly, but the physical internet connection
        coming into their house is failing. Advise them to unplug their modem from the wall,
        wait 30 seconds, and plug it back in. If that fails, tell them to call their internet provider."
      - If both are healthy, reassure the user their connection is rock solid.
    """
    gateway_ip = get_default_gateway()
    directive_parts: List[str] = []

    if not gateway_ip:
        raw_data = {"error": "Could not determine local default gateway IP"}
        directive_parts.append(
            "Explain to the user that their Mac has no active network router assigned. "
            "Their Wi-Fi is either disconnected or their Mac has not received an IP address. "
            "Tell them to turn Wi-Fi off and back on in the top-right menu bar."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Ping Gateway (5 packets)
    gw_code, gw_out, gw_err, _ = run_command([PING_PATH, "-c", "5", "-t", "5", gateway_ip], timeout=10)
    gw_metrics = _parse_ping_output(gw_out)

    # Ping 8.8.8.8 (5 packets)
    ext_code, ext_out, ext_err, _ = run_command([PING_PATH, "-c", "5", "-t", "5", "8.8.8.8"], timeout=10)
    ext_metrics = _parse_ping_output(ext_out)

    raw_data = {
        "gateway_ip": gateway_ip,
        "gateway_ping": gw_metrics,
        "external_internet_ping": ext_metrics,
    }

    # Evaluate Hotspot Fallback Awareness
    if is_hotspot_active(gateway_ip):
        directive_parts.append(HOTSPOT_DIRECTIVE)
    else:
        gw_max = gw_metrics.get("max_latency_ms")
        gw_loss = gw_metrics.get("packet_loss_percent", 100.0)
        ext_max = ext_metrics.get("max_latency_ms")
        ext_loss = ext_metrics.get("packet_loss_percent", 100.0)

        # Gateway < 5ms with 0% loss, but 8.8.8.8 drops packets or > 100ms
        gw_is_fast = (gw_loss == 0.0) and (gw_max is not None and gw_max < 5.0)
        ext_is_failing = (ext_loss > 0.0) or (ext_max is not None and ext_max > 100.0)

        if gw_is_fast and ext_is_failing:
            directive_parts.append(
                "Tell the user their home Wi-Fi is working perfectly, but the physical internet "
                "connection coming into their house is failing. Advise them to unplug their modem "
                "from the wall, wait 30 seconds, and plug it back in. If that fails, tell them to "
                "call their internet provider."
            )
        elif gw_loss > 0.0 or (gw_max is not None and gw_max >= 15.0):
            directive_parts.append(
                "Tell the user that the wireless connection between their Mac and their home Wi-Fi "
                "router is unstable or dropping data. Advise them to move closer to their router, "
                "or unplug the router from the wall for 30 seconds to restart it."
            )
        else:
            directive_parts.append(
                "Reassure the user that their connection is crystal clear. Both their local home "
                "Wi-Fi and their connection to the global internet have 0% packet loss and fast response times."
            )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Tool 6: test_dns_latency
# ==============================================================================

@mcp.tool()
def test_dns_latency(domain: str = "google.com") -> str:
    """Measures DNS lookup response latency and identifies current DNS server responsiveness.

    Executes `dig +noall +stats <domain>` to measure exact round-trip query time in milliseconds.

    CRITICAL INSTRUCTIONS FOR LLM:
    - PERSONA: You MUST act as an exceptionally friendly, patient, and jargon-free IT technician.
    - NO JARGON: Never say 'recursive resolver', 'authoritative nameserver', or 'DNS TTL'.
      Explain DNS simply as the 'internet's phone book' that turns names into computer addresses.
    - DIRECTIVE ADHERENCE: Inspect '_llm_directive'.
      - If DNS query time is greater than 50ms, instruct the LLM:
        "Explain that the internet's 'phone book' is responding very slowly, making web pages
        take a long time to start loading. Advise them to change their Mac's DNS settings to
        Google (8.8.8.8) or Cloudflare (1.1.1.1) and provide very simple, step-by-step instructions
        on how to do that in macOS System Settings."
      - If DNS query time is <= 50ms, reassure them that website address lookups are instant.
    """
    # Clean domain input
    sanitized_domain = re.sub(r"[^a-zA-Z0-9.-]", "", domain) or "google.com"
    cmd = [DIG_PATH, "+noall", "+stats", sanitized_domain]
    code, stdout, stderr, err = run_command(cmd, timeout=8)

    # Fallback to cloudflare.com if the primary domain lookup fails
    if code != 0 or "Query time:" not in stdout:
        cmd = [DIG_PATH, "+noall", "+stats", "cloudflare.com"]
        code, stdout, stderr, err = run_command(cmd, timeout=8)

    raw_data: Dict[str, Any] = {}
    directive_parts: List[str] = []

    if code != 0 or not stdout.strip():
        raw_data = {
            "error": err or stderr.strip() or "DNS lookup failed",
            "exit_code": code,
        }
        directive_parts.append(
            "Tell the user that their Mac could not reach any DNS server (the internet 'phone book'). "
            "Websites cannot translate names into addresses, so pages will not open. "
            "Give them simple steps: Open System Settings > Network > Wi-Fi > Details > DNS, "
            "click '+' and add Cloudflare (1.1.1.1) or Google (8.8.8.8)."
        )
        return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)

    # Extract Query time: X msec
    query_match = re.search(r";;\s*Query time:\s*(\d+)\s*msec", stdout)
    server_match = re.search(r";;\s*SERVER:\s*([^\n]+)", stdout)

    query_time_ms = int(query_match.group(1)) if query_match else None
    server_str = server_match.group(1).strip() if server_match else "default"

    raw_data = {
        "domain_queried": sanitized_domain,
        "query_time_ms": query_time_ms,
        "dns_server": server_str,
    }

    if query_time_ms is not None and query_time_ms > 50:
        directive_parts.append(
            "Explain that the internet's 'phone book' is responding very slowly, making web pages "
            "take a long time to start loading. Advise them to change their Mac's DNS settings to "
            "Google (8.8.8.8) or Cloudflare (1.1.1.1) and provide very simple, step-by-step instructions "
            "on how to do that in macOS System Settings."
        )
    elif query_time_ms is not None:
        directive_parts.append(
            f"Inform the user that their internet 'phone book' (DNS) is responding quickly ({query_time_ms}ms). "
            "Website names are resolving promptly with zero lookup lag."
        )
    else:
        directive_parts.append(
            "Inform the user that DNS lookups are completing, but response times could not be "
            "accurately timed."
        )

    return json.dumps({"raw_data": raw_data, "_llm_directive": " ".join(directive_parts)}, indent=2)


# ==============================================================================
# Server Entrypoint
# ==============================================================================

if __name__ == "__main__":
    # FastMCP runs on standard stdio transport by default
    mcp.run()
