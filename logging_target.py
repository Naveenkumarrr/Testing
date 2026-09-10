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

DEBUG:
    Every step (each command sent, and the raw text received back) is
    printed to the console so you can see exactly where things go wrong.
    Set DEBUG = False below to silence this and get compact output instead.
"""

import sys
import time
import getpass
import pandas as pd
import paramiko

DEBUG = True


def log(label, text):
    """Print a labeled, debug-friendly view of a step. repr() shows hidden
    characters (like \\r, \\n, prompt control codes) that are normally
    invisible in a terminal but often explain 'why did it stop responding'."""
    if not DEBUG:
        return
    print(f"    [{label}] {repr(text)}")


def send_command(shell, command, wait=1.0):
    """Send a command to the interactive shell and return whatever came back."""
    print(f"  >>> SEND: {command}")
    shell.send(command + "\n")
    time.sleep(wait)
    output = ""
    while shell.recv_ready():
        output += shell.recv(65535).decode("utf-8", errors="ignore")
        time.sleep(0.2)
    log("RECV", output)
    return output


def discover_logging_targets(shell):
    """Best-effort parse of 'show logging-target' output to get target names."""
    print("  --- Discovering logging targets ---")
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
    print(f"  --- Parsed target names: {targets} ---")
    return targets


def interactive_login(shell, username, password, timeout=20):
    """
    Some DataPower boxes show their own CLI-level login/password prompt(s)
    after the SSH session is already open (on top of SSH auth), and may
    repeat the login prompt. Instead of assuming a fixed number of prompts,
    watch the incoming text and answer whatever prompt shows up until we
    reach a normal command prompt ('>' or '#').
    """
    print("  --- Starting interactive login handshake ---")
    buffer = ""
    end_time = time.time() + timeout
    step = 0
    while time.time() < end_time:
        if shell.recv_ready():
            chunk = shell.recv(65535).decode("utf-8", errors="ignore")
            buffer += chunk
            step += 1
            log(f"login step {step} RECV", chunk)
            tail = buffer.strip().splitlines()[-1].lower() if buffer.strip() else ""
            log(f"login step {step} TAIL-LINE", tail)

            if "login" in tail or "username" in tail:
                print(f"  >>> Detected login/username prompt -> sending username")
                shell.send(username + "\n")
                buffer = ""
            elif "password" in tail:
                print(f"  >>> Detected password prompt -> sending password")
                shell.send(password + "\n")
                buffer = ""
            elif tail.endswith(">") or tail.endswith("#"):
                print(f"  >>> Detected command prompt -> login handshake done")
                return buffer
        else:
            time.sleep(0.3)
    print("  !!! Login handshake TIMED OUT waiting for a command prompt. "
          "Last buffer contents shown above (RECV) — check for unexpected "
          "banner text or a prompt format this script doesn't recognize.")
    return buffer


def process_appliance(ip, domain, targets_hint, username, password, port=22):
    print(f"\n=== {ip}  (domain: {domain}) ===")

    print("  --- Step 1: Opening SSH transport connection ---")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, port=port, username=username, password=password,
                        timeout=15, look_for_keys=False, allow_agent=False)
        print("  --- Step 1 OK: SSH transport connected ---")
    except Exception as e:
        print(f"  [FAILED at Step 1 - SSH connect] {e}")
        return

    print("  --- Step 2: Opening interactive shell channel ---")
    shell = client.invoke_shell()
    time.sleep(1.5)
    if shell.recv_ready():
        banner = shell.recv(65535).decode("utf-8", errors="ignore")
        log("initial banner", banner)

    print("  --- Step 3: CLI-level login handshake ---")
    interactive_login(shell, username, password)

    print("  --- Step 4: Entering config mode (co) ---")
    out = send_command(shell, "co", wait=1)
    if "error" in out.lower() or "invalid" in out.lower():
        print("  !!! 'co' may have failed - check RECV output above.")

    print(f"  --- Step 5: Switching to domain '{domain}' ---")
    out = send_command(shell, f"switch domain {domain}", wait=2)
    if "error" in out.lower() or "invalid" in out.lower() or "not found" in out.lower():
        print(f"  !!! 'switch domain {domain}' may have failed - check RECV output above.")

    print("  --- Step 6: Determining logging targets ---")
    if targets_hint:
        targets = [t.strip() for t in targets_hint.split(",") if t.strip()]
        print(f"  Using logging targets from Excel: {targets}")
    else:
        targets = discover_logging_targets(shell)

    if not targets:
        print("  No logging targets found — skipping this appliance. "
              "Consider filling the LoggingTargets column manually.")
    else:
        for target in targets:
            print(f"  --- Step 7: Updating logging target '{target}' ---")
            out1 = send_command(shell, f'logging target "{target}"', wait=1)
            if "error" in out1.lower() or "invalid" in out1.lower():
                print(f"  !!! 'logging target \"{target}\"' may have failed - check RECV output above.")
            out2 = send_command(shell, "local-address LANIP", wait=1)
            if "error" in out2.lower() or "invalid" in out2.lower():
                print(f"  !!! 'local-address LANIP' may have failed - check RECV output above.")
            send_command(shell, "exit", wait=1)

        print("  --- Step 8: Saving config (write memory) ---")
        out = send_command(shell, "write memory", wait=2)
        send_command(shell, "y", wait=2)
        if "error" in out.lower():
            print("  !!! 'write memory' may have failed - check RECV output above.")

    client.close()
    print(f"  === Done with {ip} ===")


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
