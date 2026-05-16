"""VPN Status Watchdog daemon package.

Small Python daemon for the OS2 project. Watches a WireGuard tunnel on
macOS through `ifconfig` and `wg show`, restarts it when it gets stuck,
and dumps everything into a log file the dashboard reads.
"""

__version__ = "1.0.0"
