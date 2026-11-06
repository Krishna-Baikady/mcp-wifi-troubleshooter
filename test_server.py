"""Unit tests for macos-wifi-troubleshooter parser and directive logic."""

import json
import re
from unittest.mock import patch
import server


def test_bandwidth_hogs_massive():
    mock_nettop_output = """time,Google Chrome.12345,50000000,20000000
time,Dropbox.555,100000,50000
time,Spotify.999,200000,10000
"""
    with patch("server.run_command", return_value=(0, mock_nettop_output, "", None)), \
         patch("server.is_hotspot_active", return_value=False):
        res_str = server.check_local_bandwidth_hogs()
        res = json.loads(res_str)
        assert "raw_data" in res
        assert "_llm_directive" in res
        assert "Google Chrome" in res["_llm_directive"]
        assert "The app Google Chrome is downloading or uploading a massive amount of data in the background." in res["_llm_directive"]
        print("✓ test_bandwidth_hogs_massive passed")


def test_bandwidth_hogs_normal():
    mock_nettop_output = """time,Terminal.100,1000,500
time,Finder.200,500,200
"""
    with patch("server.run_command", return_value=(0, mock_nettop_output, "", None)), \
         patch("server.is_hotspot_active", return_value=False):
        res_str = server.check_local_bandwidth_hogs()
        res = json.loads(res_str)
        assert "none of the applications on their Mac are hogging bandwidth" in res["_llm_directive"]
        print("✓ test_bandwidth_hogs_normal passed")


def test_wifi_metrics_blocked_signal():
    mock_airport_output = """agrCtlRSSI: -76
agrCtlNoise: -92
lastTxRate: 144
SSID: MyHomeNetwork
BSSID: 11:22:33:44:55:66
state: running
channel: 36,1
"""
    with patch("server.get_airport_binary", return_value="/fake/airport"), \
         patch("server.run_command", return_value=(0, mock_airport_output, "", None)), \
         patch("server.is_hotspot_active", return_value=False):
        res_str = server.get_wifi_metrics()
        res = json.loads(res_str)
        assert res["raw_data"]["rssi_dbm"] == -76
        assert res["raw_data"]["band"] == "5 GHz"
        expected = "Tell the user their Wi-Fi signal is getting physically blocked. Give them two simple choices: move their Mac into the same room as the Wi-Fi router, or connect to the 2.4GHz Wi-Fi network instead, which travels through walls much better."
        assert expected in res["_llm_directive"]
        print("✓ test_wifi_metrics_blocked_signal passed")


def test_wifi_metrics_hotspot():
    mock_airport_output = """agrCtlRSSI: -55
agrCtlNoise: -92
lastTxRate: 300
SSID: Alice's iPhone
BSSID: aa:bb:cc:dd:ee:ff
state: running
channel: 6
"""
    with patch("server.get_airport_binary", return_value="/fake/airport"), \
         patch("server.run_command", return_value=(0, mock_airport_output, "", None)), \
         patch("server.is_hotspot_active", return_value=True):
        res_str = server.get_wifi_metrics()
        res = json.loads(res_str)
        expected = "The user is connected to a cellular hotspot backup. Remind the user that these speed/ping metrics reflect their cellular connection, not their home ISP. Evaluate the home router strictly based on the environmental Wi-Fi scan."
        assert expected in res["_llm_directive"]
        print("✓ test_wifi_metrics_hotspot passed")


def test_scan_wifi_congestion():
    mock_scan_output = """                            SSID BSSID             RSSI CHANNEL HT CC SECURITY (auth/unicast/group)
              Neighbor_1     12:34:56:78:90:ab -50  6       Y  US WPA2(PSK/AES/AES)
              Neighbor_2     23:45:67:89:0a:bc -78  6       Y  US WPA2(PSK/AES/AES)
              Neighbor_3     34:56:78:90:ab:cd -82  6       Y  US WPA2(PSK/AES/AES)
              Neighbor_4     45:67:89:0a:bc:de -85  1       Y  US WPA2(PSK/AES/AES)
"""
    with patch("server.get_airport_binary", return_value="/fake/airport"), \
         patch("server.run_command", return_value=(0, mock_scan_output, "", None)):
        res_str = server.scan_wifi_congestion()
        res = json.loads(res_str)
        assert res["raw_data"]["most_congested_channel"] == 6
        assert res["raw_data"]["recommended_2g_channel"] == 11
        assert "Explain to the user that their neighbors' Wi-Fi routers are 'shouting over' theirs on the same frequency." in res["_llm_directive"]
        assert "change the Wi-Fi channel to" in res["_llm_directive"]
        print("✓ test_scan_wifi_congestion passed")


def test_scan_wifi_location_denied():
    with patch("server.get_airport_binary", return_value="/fake/airport"), \
         patch("server.run_command", return_value=(1, "", "Scanning requires Location Services permission.", None)):
        res_str = server.scan_wifi_congestion()
        res = json.loads(res_str)
        assert "Location Services" in res["_llm_directive"]
        print("✓ test_scan_wifi_location_denied passed")


def test_internet_quality_bufferbloat():
    mock_nq_output = json.dumps({
        "dl_throughput": 120000000,
        "ul_throughput": 20000000,
        "responsiveness": 210,
        "base_rtt": 25
    })
    with patch("server.run_command", return_value=(0, mock_nq_output, "", None)), \
         patch("server.is_hotspot_active", return_value=False):
        res_str = server.measure_internet_quality()
        res = json.loads(res_str)
        assert res["raw_data"]["responsiveness_rpm"] == 210
        expected = "Explain that their router is experiencing a 'traffic jam' (bufferbloat). A large download is clogging the connection, which is why real-time apps like Zoom are stuttering or freezing."
        assert expected in res["_llm_directive"]
        print("✓ test_internet_quality_bufferbloat passed")


def test_ping_diagnostics_isp_failure():
    mock_gw_ping = """5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 1.100/2.200/3.300/0.400 ms
"""
    mock_ext_ping = """5 packets transmitted, 4 packets received, 20.0% packet loss
round-trip min/avg/max/stddev = 15.000/45.000/125.000/30.000 ms
"""
    with patch("server.get_default_gateway", return_value="192.168.1.1"), \
         patch("server.run_command", side_effect=[
             (0, mock_gw_ping, "", None),
             (0, mock_ext_ping, "", None),
         ]), \
         patch("server.is_hotspot_active", return_value=False):
        res_str = server.ping_diagnostics()
        res = json.loads(res_str)
        expected = "Tell the user their home Wi-Fi is working perfectly, but the physical internet connection coming into their house is failing. Advise them to unplug their modem from the wall, wait 30 seconds, and plug it back in. If that fails, tell them to call their internet provider."
        assert expected in res["_llm_directive"]
        print("✓ test_ping_diagnostics_isp_failure passed")


def test_ping_diagnostics_hotspot():
    with patch("server.get_default_gateway", return_value="172.20.10.1"), \
         patch("server.run_command", return_value=(0, "5 packets transmitted, 5 received, 0% packet loss", "", None)), \
         patch("server.is_hotspot_active", return_value=True):
        res_str = server.ping_diagnostics()
        res = json.loads(res_str)
        expected = "The user is connected to a cellular hotspot backup. Remind the user that these speed/ping metrics reflect their cellular connection, not their home ISP. Evaluate the home router strictly based on the environmental Wi-Fi scan."
        assert expected in res["_llm_directive"]
        print("✓ test_ping_diagnostics_hotspot passed")


def test_dns_latency_slow():
    mock_dig_output = """;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 41234
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; Query time: 145 msec
;; SERVER: 192.168.1.1#53(192.168.1.1) (UDP)
;; WHEN: Thu Nov 05 19:30:00 PST 2026
;; MSG SIZE  rcvd: 55
"""
    with patch("server.run_command", return_value=(0, mock_dig_output, "", None)):
        res_str = server.test_dns_latency("google.com")
        res = json.loads(res_str)
        assert res["raw_data"]["query_time_ms"] == 145
        expected = "Explain that the internet's 'phone book' is responding very slowly, making web pages take a long time to start loading. Advise them to change their Mac's DNS settings to Google (8.8.8.8) or Cloudflare (1.1.1.1) and provide very simple, step-by-step instructions on how to do that in macOS System Settings."
        assert expected in res["_llm_directive"]
        print("✓ test_dns_latency_slow passed")


if __name__ == "__main__":
    test_bandwidth_hogs_massive()
    test_bandwidth_hogs_normal()
    test_wifi_metrics_blocked_signal()
    test_wifi_metrics_hotspot()
    test_scan_wifi_congestion()
    test_scan_wifi_location_denied()
    test_internet_quality_bufferbloat()
    test_ping_diagnostics_isp_failure()
    test_ping_diagnostics_hotspot()
    test_dns_latency_slow()
    print("\nALL 10 UNIT TESTS PASSED SUCCESSFULLY!")

