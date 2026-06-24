import pandas as pd
import sys

INPUT_FILE  = "system_conf.xlsx"
OUTPUT_FILE = "syslog-ng.conf"

def sanitize_name(value: str) -> str:
    """Convert a string to a safe syslog-ng identifier (no hyphens, spaces, dots)."""
    return (str(value).strip()
            .replace(" ", "").replace("-", "")
            .replace(".", "").replace("/", "")
            .replace("(", "").replace(")", ""))

def make_unique(base: str, seen: dict) -> str:
    """
    Return a unique identifier.
    If 'base' was seen before, append _2, _3, ... until unique.
    Records the result in 'seen'.
    """
    if base not in seen:
        seen[base] = 1
        return base
    seen[base] += 1
    return f"{base}_{seen[base]}"

def generate():
    # ── Load Excel ──────────────────────────────────────────────────────────
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

    # ── Duplicate-name trackers ──────────────────────────────────────────────
    seen_filter_bases = {}   # base_name -> count   (for unique filter names)
    seen_dest_bases   = {}   # base_name -> count   (for unique dest names)
    seen_dest_paths   = {}   # log_path  -> dest_name (reuse same dest for same path)

    filter_blocks  = []
    dest_blocks    = []
    log_statements = []

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        domain         = get(row, "domain")
        target_name    = get(row, "target-name")
        log_path       = get(row, "log server path")
        facility       = get(row, "facility")
        remote_addr    = get(row, "remote-address")
        remote_port    = get(row, "remote-port")
        local_ident    = get(row, "local-ident")
        appliance_name = get(row, "appliance-name")
        appliance_ip   = get(row, "appliance-ip")

        # ── Unique filter name ───────────────────────────────────────────────
        filter_base = "f_" + (sanitize_name(local_ident) if local_ident else f"row{i}")
        filter_name = make_unique(filter_base, seen_filter_bases)

        # ── Filter condition ─────────────────────────────────────────────────
        # IMPORTANT: the source uses flags(no-parse), so $PROGRAM / $HOST are
        # never populated by syslog-ng's parser. Matching on value("PROGRAM")
        # or value("HOST") will therefore NEVER match. Instead, match against
        # the default field (the raw message text) and combine multiple
        # identifiers with a regex alternation "|", exactly like the known
        # working config:
        #     filter f_x { match("PU_IR1_syslogs"); };
        #     # multiple terms: match("dptzt01-wag1|172.23.39.160");
        match_terms = []
        if local_ident:
            match_terms.append(local_ident)
        if appliance_ip:
            match_terms.append(appliance_ip)

        if not match_terms:
            filter_expr = 'match(".")'
        else:
            pattern = "|".join(match_terms)
            filter_expr = f'match("{pattern}")'

        comment = (
            f"# Domain    : {domain}\n"
            f"# Target    : {target_name}\n"
            f"# Appliance : {appliance_name} ({appliance_ip})\n"
            f"# Facility  : {facility}\n"
            f"# Remote    : {remote_addr}:{remote_port}\n"
            f"# Ident     : {local_ident}\n"
        )

        filter_blocks.append(
            f"{comment}"
            f"filter {filter_name} {{\n"
            f"    {filter_expr};\n"
            f"}};\n"
        )

        # ── Destination (reuse if same path) ─────────────────────────────────
        if log_path:
            if log_path in seen_dest_paths:
                dest_name = seen_dest_paths[log_path]   # already written
            else:
                dest_base = "d_" + (sanitize_name(local_ident) if local_ident else f"row{i}")
                dest_name = make_unique(dest_base, seen_dest_bases)
                seen_dest_paths[log_path] = dest_name
                dest_blocks.append(
                    f"# Destination: {domain} / {target_name}\n"
                    f"destination {dest_name} {{\n"
                    f"    file(\n"
                    f'        "{log_path}"\n'
                    f"        create-dirs(yes)\n"
                    f"        perm(0644)\n"
                    f"        dir-perm(0755)\n"
                    # NOTE: with flags(no-parse) on the source, $PROGRAM will
                    # be empty and $HOST will just be the sender's IP (no
                    # real hostname parsing happens). If you need the actual
                    # DataPower host/program names, parse the message
                    # upstream (drop no-parse + use a pattern-db/parser), or
                    # just log the raw line with $MSG / $MSGONLY.
                    f'        template("$ISODATE $HOST $PROGRAM: $MSG\\n")\n'
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
            f"destination({dest_name}); flags(final); }};"
        )

    # ── Write output file ────────────────────────────────────────────────────
    with open(OUTPUT_FILE, "w") as f:

        # Header — version set to 4.6 to match the running syslog-ng
        f.write("""\
@version: 4.6
@include "scl.conf"

# =============================================================================
# syslog-ng configuration – auto-generated by generate_syslog_ng.py
# Platform : SUSE Linux Enterprise 15.x  (syslog-ng 4.6)
# Source   : system_conf.xlsx
# NOTE     : @define allow-config-dups 1 is NOT used; all names are unique.
# NOTE     : Source uses flags(no-parse) -> filters match on raw message
#            text (NOT value("PROGRAM")/value("HOST")), and log{} blocks
#            use flags(final) so matched messages stop falling through.
# =============================================================================

# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Source: DataPower UDP listener (shared by all domains)
# ---------------------------------------------------------------------------
source s_datapower {
    network(
        ip("0.0.0.0")
        port(514)
        transport("udp")
        flags(no-parse)
    );
};

# ---------------------------------------------------------------------------
# Filters (one per row – names de-duplicated with _2, _3 suffix if needed)
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
    print(f"    Entries processed : {len(df)}")
    print(f"    Unique filters    : {len(filter_blocks)}")
    print(f"    Unique destinations: {len(dest_blocks)}")
    print(f"\n    Validate with : syslog-ng --syntax-only -f {OUTPUT_FILE}")
    print(f"    Then reload   : systemctl reload syslog-ng")

if __name__ == "__main__":
    generate()