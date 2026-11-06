# macOS Wi-Fi Troubleshooter MCP Server

A zero-configuration Model Context Protocol (MCP) server for automated macOS network diagnostics.
It uses native macOS terminal commands to detect slowdowns, interference, and internet outages.

## Features

- **Zero-Configuration Persona**: Dictates LLM tone directly through tool docstrings and runtime response directives.
- **Jargon-Free Advice**: Translates complex networking metrics into plain, friendly explanations anyone can understand.
- **Hotspot Fallback Awareness**: Automatically detects iPhone personal hotspots (`172.20.10.1`) and prevents false home router alarms.
- **Native macOS Commands**: Uses built-in system tools (`nettop`, `airport`, `networkquality`, `route`, `ping`, and `dig`).

## Tools

1. **`check_local_bandwidth_hogs`**: Runs `nettop` to find the top 5 network-consuming applications.
2. **`get_wifi_metrics`**: Runs `airport -I` to check signal strength (RSSI), noise, and Wi-Fi band.
3. **`scan_wifi_congestion`**: Runs `airport -s` to detect neighboring Wi-Fi overlap and find clear channels.
4. **`measure_internet_quality`**: Runs `networkquality -c` to measure download/upload speed and bufferbloat (RPM).
5. **`ping_diagnostics`**: Pings your router and `8.8.8.8` to separate home Wi-Fi issues from ISP outages.
6. **`test_dns_latency`**: Runs `dig` to measure lookup speeds and catch slow DNS servers.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running the Server

```bash
python3 server.py
```

## MCP Client Configuration

Add the server to your MCP client settings (e.g. Claude Desktop or Antigravity):

```json
{
  "mcpServers": {
    "wifi-troubleshooter": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp-wifi-troubleshooter/server.py"]
    }
  }
}
```

## Running Tests

Verify all parsers and directives:

```bash
python3 test_server.py
```

## License

This project is licensed under the [MIT License](LICENSE).


