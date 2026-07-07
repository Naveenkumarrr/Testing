import pandas as pd
import sys

INPUT_FILE  = "system_conf.xlsx"
OUTPUT_FILE = "syslog-ng.conf"

def sanitize_name(value: str) -> str:
    return (str(value).strip()
            .replace(" ", "").replace("-", "")
            .replace(".", "").replace("/", "")
            .replace("(", "").replace(")", ""))

def make_unique(base: str, seen: dict) -> str:
    if base not in seen:
        seen[base] = 1
        return base
    seen[base] += 1
    return f"{base}_{seen[base]}"

def generate():
    try:
        df = pd.read_excel(INPUT_FILE, engine="openpyxl")
    except FileNotFoundError:
        print(f"ERROR: '{INPUT_FILE}' not found in the current directory.")
        sys.exit(1)

    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(how="all")

    print(f"Loaded {len(df)} rows from {INPUT_FILE}")
    print(f"Columns: {list(df.columns)}\n")

    required_cols = [
        "domain", "target-name", "log server path",
        "facility", "remote-address", "remote-port",
        "local-ident", "appliance-name", "appliance-ip"
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"WARNING: Missing columns: {missing}")
        print("Proceeding – those fields will be empty.\n")

    def get(row, col):
        if col not in df.columns:
            return ""
        val = row.get(col, "")
        return "" if pd.isna(val) else str(val).strip()

    seen_filter_bases = {}
    seen_dest_bases   = {}
    seen_dest_paths   = {}

    filter_blocks  = []
    dest_blocks    = []
    log_statements = []

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        domain         = get(row, "domain")
        target_name    = get(row, "target-name")
        log_path       = get(row, "log server path")
        facility       = get(row, "facility") or "local1.info"  # fallback if empty
        remote_addr    = get(row, "remote-address")
        remote_port    = get(row, "remote-port")
        local_ident    = get(row, "local-ident")
        appliance_name = get(row, "appliance-name")
        appliance_ip   = get(row, "appliance-ip")

        filter_base = "f_" + (sanitize_name(local_ident) if local_ident else f"row{i}")
        filter_name = make_unique(filter_base, seen_filter_bases)

        rewrite_strip_name  = f"r_strip_priority_{filter_name}"
        rewrite_insert_name = f"r_insert_ip_{filter_name}"

        comment = (
            f"# Domain    : {domain}\n"
            f"# Target    : {target_name}\n"
            f"# Appliance : {appliance_name} ({appliance_ip})\n"
            f"# Facility  : {facility}\n"
            f"# Remote    : {remote_addr}:{remote_port}\n"
            f"# Ident     : {local_ident}\n"
        )

        if local_ident:
            filter_expr = f'match("{local_ident}" value("MSG"))'
        else:
            filter_expr = 'match("." value("MSG"))'

        filter_blocks.append(
            f"{comment}"
            f"filter {filter_name} {{\n"
            f"    {filter_expr};\n"
            f"}};\n"
            f"\n"
            f"rewrite {rewrite_strip_name} {{\n"
            f"    subst(\n"
            f'        "^<[0-9]+>",\n'
            f'        "",\n'
            f'        value("MSG")\n'
            f'        type("pcre")\n'
            f"    );\n"
            f"}};\n"
            f"\n"
            f"rewrite {rewrite_insert_name} {{\n"
            f"    subst(\n"
            f'        "{local_ident} ",\n'
            f'        "{local_ident}/${{SOURCEIP}} {facility} ",\n'
            f'        value("MSG")\n'
            f'        type("pcre")\n'
            f"    );\n"
            f"}};\n"
        )

        if log_path:
            if log_path in seen_dest_paths:
                dest_name = seen_dest_paths[log_path]
            else:
                dest_base = "d_" + (sanitize_name(local_ident) if local_ident else f"row{i}")
                dest_name = make_unique(dest_base, seen_dest_bases)
                seen_dest_paths[log_path] = dest_name
                dest_blocks.append(
                    f"# Destination: {domain} / {target_name}\n"
                    f"destination {dest_name} {{\n"
                    f"    file(\n"
                    f'        "{log_path}"\n'
                    f"        create_dirs(yes)\n"
                    f"        perm(0644)\n"
                    f"        dir_perm(0755)\n"
                    f'        template("${{MSG}}\\n")\n'
                    f"    );\n"
                    f"}};\n"
                )
        else:
            dest_base = "d_" + (sanitize_name(local_ident) if local_ident else f"row{i}")
            dest_name = make_unique(dest_base, seen_dest_bases)
            dest_blocks.append(
                f"# Destination: {domain} / {target_name} (no path – fallback)\n"
                f"destination {dest_name} {{\n"
                f'    syslog("127.0.0.1" port(514) transport("udp"));\n'
                f"}};\n"
            )

        log_statements.append(
            f"log {{ source(s_datapower); filter({filter_name}); "
            f"rewrite({rewrite_strip_name}); rewrite({rewrite_insert_name}); "
            f"destination({dest_name}); flags(final); }};"
        )

    with open(OUTPUT_FILE, "w") as f:

        f.write("""\
@version: 4.6
@include "scl.conf"

# =============================================================================
# syslog-ng configuration – auto-generated by generate_syslog_ng.py
# Platform : SUSE Linux Enterprise 15.x  (syslog-ng 4.6)
# Source   : system_conf.xlsx
# NOTE     : Source uses flags(no-parse) -> filters match on raw MSG text.
#            Two rewrites per entry:
#              1. r_strip_priority_* : removes <NNN> syslog priority prefix
#              2. r_insert_ip_*      : inserts SOURCEIP and facility after ident
# =============================================================================

options {
    flush_lines(0);
    time_reopen(10);
    log_fifo_size(1000);
    chain_hostnames(off);
    use_dns(no);
    use_fqdn(no);
    create_dirs(yes);
    keep_hostname(yes);
};

source s_datapower {
    network(
        ip("0.0.0.0")
        port(514)
        transport("udp")
        flags(no-parse)
    );
};

# ---------------------------------------------------------------------------
# Filters and Rewrites (one set per row)
# ---------------------------------------------------------------------------
""")

        for blk in filter_blocks:
            f.write(blk + "\n")

        f.write("""\
# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
""")
        for blk in dest_blocks:
            f.write(blk + "\n")

        f.write("""\
# ---------------------------------------------------------------------------
# Log routing
# ---------------------------------------------------------------------------
""")
        for stmt in log_statements:
            f.write(stmt + "\n")

    print(f"✅  '{OUTPUT_FILE}' generated successfully.")
    print(f"    Entries processed  : {len(df)}")
    print(f"    Unique filters     : {len(filter_blocks)}")
    print(f"    Unique destinations: {len(dest_blocks)}")
    print(f"\n    Validate with : syslog-ng --syntax-only -f {OUTPUT_FILE}")
    print(f"    Then reload   : systemctl reload syslog-ng")

if __name__ == "__main__":
    generate()
