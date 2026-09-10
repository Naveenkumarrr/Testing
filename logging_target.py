"""
datapower_update_logging_target.py

Reads an Excel sheet of DataPower appliances and, for each row, SSHes into the
appliance, switches to the given domain, and sets local-address to "LANIP"
on the logging target(s) in that domain, then does 'write memory'.

Excel sheet requirements (column names, case-insensitive):
    - IP               : appliance management IP or hostname
    - Domain           : DataPower domain name to switch into
    - LoggingTargets   : (optional) comma-separated logging target names,
                          e.g. "syslog1, nfs-log"
                          If this column is empty/missing for a row, the
                          script will try to auto-discover targets by
                          running 'show logging-target' in that domain.

Install dependencies first:
    pip install paramiko pandas openpyxl --break-system-packages

Usage:
    python datapower_update_logging_target.py appliances.xlsx
    (it will prompt for username/password, used for all appliances)
"""

import sys
import time
import getpass
import pandas as pd
import paramiko


def send_command(shell, command, wait=1.0):
    """Send a command to the interactive shell and return whatever came back."""
    shell.send(command + "\n")
    time.sleep(wait)
    output = ""
    while shell.recv_ready():
        output += shell.recv(65535).decode("utf-8", errors="ignore")
        time.sleep(0.2)
    return output


def discover_logging_targets(shell):
    """Best-effort parse of 'show logging-target' output to get target names."""
    output = send_command(shell, "show logging-target", wait=2)
    targets = []
    for line in output.splitlines():
        line = line.strip()
        # Skip echoed command, prompts, and headers
        if not line or line.startswith("show") or line.endswith(">") or "#" in line:
            continue
        first_token = line.split()[0] if line.split() else ""
        if first_token and first_token.lower() not in ("name", "-----", "logging-target"):
            targets.append(first_token)
    return targets


def process_appliance(ip, domain, targets_hint, username, password, port=22):
    print(f"\n=== {ip}  (domain: {domain}) ===")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, port=port, username=username, password=password,
                        timeout=15, look_for_keys=False, allow_agent=False)
    except Exception as e:
        print(f"  [FAILED to connect] {e}")
        return

    shell = client.invoke_shell()
    time.sleep(2)
    if shell.recv_ready():
        shell.recv(65535)  # clear login banner

    send_command(shell, "co", wait=1)
    send_command(shell, f"switch domain {domain}", wait=2)

    if targets_hint:
        targets = [t.strip() for t in targets_hint.split(",") if t.strip()]
        print(f"  Using logging targets from Excel: {targets}")
    else:
        targets = discover_logging_targets(shell)
        print(f"  Auto-discovered logging targets: {targets}")

    if not targets:
        print("  No logging targets found — skipping this appliance. "
              "Consider filling the LoggingTargets column manually.")
    else:
        for target in targets:
            print(f"  Setting local-address LANIP on logging target: {target}")
            send_command(shell, f'logging target "{target}"', wait=1)
            send_command(shell, "local-address LANIP", wait=1)
            send_command(shell, "exit", wait=1)

        send_command(shell, "write memory", wait=2)
        send_command(shell, "y", wait=2)

    client.close()
    print(f"  Done with {ip}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python datapower_update_logging_target.py <excel_file>")
        sys.exit(1)

    excel_path = sys.argv[1]
    username = input("DataPower username: ").strip()
    password = getpass.getpass("DataPower password: ")

    df = pd.read_excel(excel_path)
    cols_lower = {c.lower(): c for c in df.columns}

    if "ip" not in cols_lower or "domain" not in cols_lower:
        print("Excel must have at least 'IP' and 'Domain' columns.")
        sys.exit(1)

    targets_col = cols_lower.get("loggingtargets")

    for _, row in df.iterrows():
        ip = str(row[cols_lower["ip"]]).strip()
        domain = str(row[cols_lower["domain"]]).strip()
        if not ip or ip.lower() == "nan":
            continue
        targets_hint = ""
        if targets_col and pd.notna(row[targets_col]):
            targets_hint = str(row[targets_col]).strip()

        process_appliance(ip, domain, targets_hint, username, password)


if __name__ == "__main__":
    main()