"""mcp-xray CLI: analyze | consolidate | validate | report (PLAN S5)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, connect
from .finding import Finding
from .grade import Grader
from .inventory import Inventory
from .orchestrator import (
    ConsolidateProbe,
    StaticHygieneProbe,
    build_context,
    run,
    run_phased,
)
from .phases import build_phased, load_phases_manifest
from .chat import make_chat_client
from .pricing import load_prices
from .report import build_report, render_markdown, standard_run_name, write_run
from .vendors import ANTHROPIC, OPENAI, vendor_for
from .validate import validate


# --- shared loaders ------------------------------------------------------
def _parse_headers(args) -> dict | None:
    """Collect auth/custom HTTP headers for live HTTP/SSE transports from
    repeatable ``--header "Name: value"`` flags, plus the ``MCP_XRAY_HTTP_HEADER``
    env fallback (one ``Name: value`` per line). Used to reach authed MCP servers
    (e.g. ``--header "Authorization: Bearer <token>"``). Returns None when empty."""
    raw: list[str] = list(getattr(args, "header", None) or [])
    env = os.environ.get("MCP_XRAY_HTTP_HEADER")
    if env:
        raw.extend(line for line in env.splitlines() if line.strip())
    headers: dict[str, str] = {}
    for item in raw:
        if ":" not in item:
            raise SystemExit(f'error: --header must be "Name: value", got {item!r}')
        name, _, value = item.partition(":")
        name = name.strip()
        if not name:
            raise SystemExit(f'error: --header has empty name: {item!r}')
        headers[name] = value.strip()
    return headers or None


def _load_inventory(args) -> Inventory:
    if getattr(args, "tools_json", None):
        return connect.from_tools_json(args.tools_json)
    if getattr(args, "stdio", None):
        return connect.from_stdio(args.stdio)
    if getattr(args, "http", None):
        return connect.from_http(args.http, sse=False, headers=_parse_headers(args))
    if getattr(args, "sse", None):
        return connect.from_http(args.sse, sse=True, headers=_parse_headers(args))
    raise SystemExit("error: one of --tools-json / --stdio / --http / --sse is required")


def _load_yaml_list(path: str | None, key: str) -> list[dict] | None:
    if not path:
        return None
    import yaml

    data = yaml.safe_load(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get(key, [])
    return list(data) if data else None


# Per-vendor env key + SDK + install hint for the behavioral probe.
_VENDOR_REQUIREMENTS = {
    ANTHROPIC: ("ANTHROPIC_API_KEY", "anthropic", "mcp-xray[api]"),
    OPENAI: ("OPENAI_API_KEY", "openai", "mcp-xray[openai]"),
}


def _maybe_client(args):
    """Build the vendor chat client for ``--model`` when behavioral LLM probing
    is requested and its key + SDK are present. Returns (client, why_not)."""
    if not getattr(args, "llm", False):
        return None, "llm not requested (--llm)"
    vendor = vendor_for(getattr(args, "model", None))
    req = _VENDOR_REQUIREMENTS.get(vendor)
    if req is None:
        return None, f"no behavioral-probe vendor for model {getattr(args, 'model', None)!r}"
    env_key, sdk, extra = req
    if not os.environ.get(env_key):
        return None, f"{env_key} not set"
    try:
        __import__(sdk)
    except ImportError:
        return None, f"{sdk} SDK not installed (pip install {extra})"
    try:
        return make_chat_client(args.model), None
    except Exception as e:  # pragma: no cover - construction guarded above
        return None, str(e)


def _is_live(args) -> bool:
    return bool(getattr(args, "stdio", None) or getattr(args, "http", None) or getattr(args, "sse", None))


def _persist_inputs(out_dir: str, args, result) -> None:
    """Copy the run's INPUT into ``<out_dir>/dumps/`` so the run folder
    reproduces its own report offline - no live server, no re-capture.

      phased  -> the phases manifest + each referenced tools-json (relative
                 names preserved, so ``analyze --phases <out>/dumps/phases.yaml``
                 just works).
      flat    -> the captured surface as ``tools.json`` (``analyze
                 --tools-json <out>/dumps/tools.json``).
    """
    dst = Path(out_dir) / "dumps"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        if getattr(args, "phases", None):
            import yaml
            man = Path(args.phases)
            data = yaml.safe_load(man.read_text())
            mapping = data.get("phases", data) if isinstance(data, dict) else {}
            for rel in mapping.values():
                src = man.parent / rel
                if src.is_file():
                    shutil.copy2(src, dst / Path(rel).name)
            shutil.copy2(man, dst / man.name)
        elif getattr(result, "inventory", None) is not None:
            (dst / "tools.json").write_text(
                json.dumps(result.inventory.to_tools_json(), indent=2, default=str)
            )
        print(f"wrote {dst}/ (replayable input)")
    except Exception as e:  # best-effort - never fail the run over archival
        print(f"[warn] could not persist run inputs: {e}", file=sys.stderr)


def _probe_cache_path(args, fingerprint: str) -> str:
    """Stable resume-cache path for the behavioral probe, keyed by surface
    fingerprint + model so a changed surface/model gets a fresh cache. Lives
    under the runs base (or --out, else CWD) in a shared .probe-cache/ dir so it
    persists across runs and is NOT clobbered by the per-version run folder."""
    base = getattr(args, "runs_dir", None) or getattr(args, "out", None) or "."
    model = (args.model or "model").replace("/", "_").replace(":", "_")
    return str(Path(base) / ".probe-cache" / f"{fingerprint}-{model}.jsonl")


def _result_size_findings(measures: list[dict]) -> list[Finding]:
    """Pure: result-size measurements (from connect.measure_result_sizes) ->
    result_size Findings. Kept pure so a test can pin the contract."""
    out: list[Finding] = []
    for m in measures:
        meas: dict = {"tool": m.get("tool")}
        if "error" in m:
            meas["error"] = m["error"]
        else:
            meas["chars"] = m.get("chars", 0)
            meas["bytes"] = m.get("bytes", 0)
        out.append(
            Finding(
                probe="result_size",
                kind="result_size",
                target=m.get("tool"),
                measurement=meas,
                confidence=0.9,
                detail={"args": m.get("args", {})},
            )
        )
    return out


def _measure_result_sizes(args, manifest: list[dict], result) -> list[Finding]:
    """Call each --call-manifest tool once on the LIVE server and measure result
    sizes. Returns [] (with a warning) when there's no live, non-phased server."""
    if getattr(args, "phases", None) or not _is_live(args):
        print(
            "[warn] --call-manifest needs a LIVE, non-phased run "
            "(--stdio / --http / --sse) to call tools - result sizes not measured.",
            file=sys.stderr,
        )
        return []
    try:
        if args.stdio:
            measures = connect.result_sizes_stdio(args.stdio, manifest)
        elif args.http:
            measures = connect.result_sizes_http(args.http, manifest, headers=_parse_headers(args))
        else:  # sse
            measures = connect.result_sizes_http(args.sse, manifest, sse=True, headers=_parse_headers(args))
    except Exception as e:  # connection/protocol failure - degrade, don't crash
        print(f"[warn] result-size probing failed: {e}", file=sys.stderr)
        return []
    findings = _result_size_findings(measures)
    if findings and "result_size" not in result.ran:
        result.ran.append("result_size")
    return findings


# --- subcommands ---------------------------------------------------------
def cmd_analyze(args) -> int:
    client, why = _maybe_client(args)
    if getattr(args, "llm", False) and client is None:
        print(f"[warn] behavioral probe disabled: {why}", file=sys.stderr)

    queries = _load_yaml_list(args.queries, "queries")
    manifest = _load_yaml_list(args.call_manifest, "calls")

    if args.token_backend == "api" and not args.model:
        raise SystemExit("error: --token-backend api requires --model (the client's production model)")

    # Degrade gracefully (PLAN S3.4): a requested api backend that can't run
    # falls back to the offline ESTIMATE with a loud warning, rather than
    # crashing. Anthropic counting needs ANTHROPIC_API_KEY; OpenAI counts
    # locally via tiktoken (no key, no credits) so it never needs to degrade.
    if args.token_backend == "api" and vendor_for(args.model) == ANTHROPIC and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[warn] --token-backend api needs ANTHROPIC_API_KEY for Anthropic models; "
            "falling back to offline ESTIMATE (not authoritative)",
            file=sys.stderr,
        )
        args.token_backend = "offline"

    # Behavioral-probe tuning knobs (None -> probe default). --samples trades
    # selection-score stability for linearly more LLM calls.
    config = {}
    if getattr(args, "samples", None) is not None:
        if args.samples < 1:
            raise SystemExit("error: --samples must be >= 1")
        config["noise_samples"] = args.samples

    if getattr(args, "phases", None):
        # Phase-aware audit: per-phase tax + union quality + progressive credit.
        phased = build_phased(load_phases_manifest(args.phases))
        if getattr(args, "resume", False):
            config["probe_cache"] = _probe_cache_path(args, phased.union.fingerprint())
        result = run_phased(
            phased,
            token_backend=args.token_backend,
            model=args.model,
            chat_client=client,
            queries=queries,
            call_manifest=manifest,
            config_path=getattr(args, "client_config", None),
            config=config,
        )
    else:
        inv = _load_inventory(args)
        if getattr(args, "resume", False):
            config["probe_cache"] = _probe_cache_path(args, inv.fingerprint())
        result = run(
            inv,
            token_backend=args.token_backend,
            model=args.model,
            chat_client=client,
            queries=queries,
            call_manifest=manifest,
            config_path=getattr(args, "client_config", None),
            live=_is_live(args),
            config=config,
        )

    # Result-size probing (--call-manifest): tool OUTPUTS cost context too. Call
    # each operator-confirmed tool once on the LIVE server and measure its result
    # size. Needs a live, non-phased run (no server to call otherwise).
    if manifest:
        result.findings.extend(_measure_result_sizes(args, manifest, result))

    # Resolve the per-model price table once and stash it on the run context so
    # build_report prices the context tax against the chosen --model. Off by
    # default (built-in table, deterministic); --refresh-prices fetches live.
    price_table, price_meta = load_prices(refresh=getattr(args, "refresh_prices", False), model=args.model)
    if price_meta.get("note"):
        print(f"[warn] prices: {price_meta['note']}", file=sys.stderr)
    result.context["price_table"] = price_table
    result.context["price_meta"] = price_meta

    grade = Grader().grade(result.findings, ran=result.ran)

    out_dir = args.out
    if getattr(args, "runs_dir", None):
        # Consistent auto-name: <server-version>-<date>-<timestamp> under the base.
        out_dir = str(Path(args.runs_dir) / standard_run_name(build_report(result, grade)))

    if out_dir:
        paths = write_run(out_dir, result, grade)
        print(f"wrote {paths['json']}")
        print(f"wrote {paths['md']}")
        # Persist the run's INPUT next to its report so the run folder is
        # self-contained and replayable offline (no live server / re-capture).
        _persist_inputs(out_dir, args, result)
    if args.json:
        print(json.dumps(build_report(result, grade), indent=2, default=str))
    elif not out_dir:
        print(render_markdown(build_report(result, grade)))
    else:
        print(f"\nGrade: {grade.letter} ({grade.overall}/100)")
    return 0


def cmd_consolidate(args) -> int:
    inv = _load_inventory(args)
    ctx = build_context(inv, token_backend=args.token_backend, model=args.model, live=_is_live(args))
    findings = ConsolidateProbe().run(ctx)
    # Static smells inform descriptions/sharpen proposals -- include for context.
    findings += [f for f in StaticHygieneProbe().run(ctx) if f.kind == "schema_smell"]
    out = [f.to_dict() for f in findings]
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_validate(args) -> int:
    before = connect.from_tools_json(args.before)
    after = connect.from_tools_json(args.after)
    queries = _load_yaml_list(args.queries, "queries")
    # Build the LLM client from --model (not just the llm flag) - else
    # vendor_for(None) is UNKNOWN, no client is made, and selection accuracy is
    # silently skipped despite --queries.
    client, why = _maybe_client(argparse.Namespace(llm=bool(queries), model=args.model))
    if queries and client is None:
        print(f"[warn] selection accuracy NOT measured: {why}", file=sys.stderr)
    delta = validate(
        before,
        after,
        queries=queries,
        token_backend=args.token_backend,
        model=args.model,
        chat_client=client,
    )
    print(json.dumps(delta.__dict__, indent=2, default=str))
    return 0


def cmd_capture_phases(args) -> int:
    """Walk a phase-swapped server (one session), snapshot each phase, and write
    per-phase dumps + a phases.yaml manifest ready for `analyze --phases`."""
    import yaml

    spec_doc = yaml.safe_load(Path(args.capture).read_text())
    spec = spec_doc.get("phases", spec_doc) if isinstance(spec_doc, dict) else spec_doc
    if not isinstance(spec, list) or not spec:
        raise SystemExit("error: capture manifest must list phases: [{name, advance?}, ...]")

    if not (args.stdio or args.http or args.sse):
        raise SystemExit("error: one of --stdio / --http / --sse is required")
    try:
        if args.stdio:
            phase_invs = connect.capture_phases_stdio(args.stdio, spec)
        elif args.http:
            phase_invs = connect.capture_phases_http(args.http, spec, headers=_parse_headers(args))
        else:
            phase_invs = connect.capture_phases_http(args.sse, spec, sse=True, headers=_parse_headers(args))
    except BaseException as e:  # noqa: BLE001 - anyio wraps errors in ExceptionGroup
        # Surface the root cause cleanly instead of a task-group traceback. The
        # common case: an advance tool (e.g. load_model) isn't exposed because
        # the target isn't in multi-model mode.
        msgs = []

        def _collect(exc):
            sub = getattr(exc, "exceptions", None)
            if sub:
                for s in sub:
                    _collect(s)
            else:
                msgs.append(str(exc))

        _collect(e)
        detail = "; ".join(m for m in msgs if m) or str(e)
        raise SystemExit(f"capture-phases failed: {detail}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"phases": {}}
    for name, inv in phase_invs.items():
        p = out / f"{name}.json"
        p.write_text(json.dumps(inv.to_tools_json(), indent=2, default=str))
        manifest["phases"][name] = f"{name}.json"
        print(f"captured phase '{name}': {len(inv.tools)} tools -> {p}", file=sys.stderr)
    man_path = out / "phases.yaml"
    man_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    print(f"wrote {man_path}")
    print(f"next: mcp-xray analyze --phases {man_path}")
    return 0


def cmd_dump(args) -> int:
    """Connect to a live server and write its tool list as a tools-json file
    that round-trips into `analyze --tools-json`."""
    inv = _load_inventory(args)
    payload = json.dumps(inv.to_tools_json(), indent=2, default=str)
    if args.out:
        Path(args.out).write_text(payload)
        print(f"wrote {args.out} ({len(inv.tools)} tools)", file=sys.stderr)
    else:
        print(payload)
    return 0


def cmd_report(args) -> int:
    run_dir = Path(args.run)
    report_path = run_dir / "report.json"
    if not report_path.exists():
        raise SystemExit(f"error: {report_path} not found")
    report = json.loads(report_path.read_text())
    md = render_markdown(report)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}")
    else:
        print(md)
    return 0


# --- parser --------------------------------------------------------------
def _add_source_args(p, *, transports=True):
    p.add_argument("--tools-json", help="offline tools/list dump (static + consolidation half)")
    if transports:
        p.add_argument("--stdio", help='spawn a local MCP server, e.g. "gmail-mcp serve"')
        p.add_argument("--http", help="streamable HTTP MCP endpoint URL")
        p.add_argument("--sse", help="SSE MCP endpoint URL")
        p.add_argument(
            "--header",
            action="append",
            metavar='"Name: value"',
            help='extra HTTP header for --http/--sse, repeatable; for authed servers '
                 'e.g. --header "Authorization: Bearer <token>" (also MCP_XRAY_HTTP_HEADER env)',
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcp-xray", description=__doc__)
    p.add_argument("--version", action="version", version=f"mcp-xray {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="full audit, every probe that can run")
    _add_source_args(a)
    a.add_argument("--phases", help="phases manifest (YAML: phase-name -> tools-json) for a phase-swapped surface")
    a.add_argument("--queries", help="labeled golden queries (YAML) -> enables labeled M3")
    a.add_argument("--call-manifest", help="operator-confirmed safe calls (YAML) -> result-size probing")
    a.add_argument("--client-config", help="client MCP config path -> enables wrapped sensors (mcp-checkup / token-analyzer) when their binaries are installed")
    a.add_argument("--model", help="model for token counting / LLM probes (client's production model)")
    a.add_argument("--token-backend", choices=["offline", "api"], default="offline")
    a.add_argument("--llm", action="store_true", help="enable behavioral LLM probe (needs ANTHROPIC_API_KEY)")
    a.add_argument("--samples", type=int, default=None,
                   help="tool-choice samples per behavioral probe (default 3); higher = steadier selection score, linearly more LLM calls")
    a.add_argument("--resume", action="store_true",
                   help="cache behavioral-probe samples (under <runs-dir>/.probe-cache, keyed by surface fingerprint + model) and reuse them - a re-run after a crash/credit-out resumes without re-paying for completed samples")
    a.add_argument("--refresh-prices", action="store_true", help="fetch live list prices for the cost figure (default: built-in table)")
    a.add_argument("--out", help="explicit run directory to write report.json + report.md")
    a.add_argument("--runs-dir", help="base dir; writes to <base>/<server-version>/ (one folder per version; full timestamp is inside the report)")
    a.add_argument("--json", action="store_true", help="print report.json to stdout")
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("consolidate", help="just the capability-reduction analysis")
    _add_source_args(c)
    c.add_argument("--model")
    c.add_argument("--token-backend", choices=["offline", "api"], default="offline")
    c.set_defaults(func=cmd_consolidate)

    v = sub.add_parser("validate", help="before/after harness for a proposed merge")
    v.add_argument("--before", required=True, help="tools-json of the current surface")
    v.add_argument("--after", required=True, help="tools-json of the merged surface")
    v.add_argument("--queries", help="labeled golden queries (YAML)")
    v.add_argument("--model")
    v.add_argument("--token-backend", choices=["offline", "api"], default="offline")
    v.set_defaults(func=cmd_validate)

    d = sub.add_parser("dump", help="connect to a live server, write its tool list as tools-json")
    _add_source_args(d)
    d.add_argument("--out", help="write tools-json to this path (default: stdout)")
    d.set_defaults(func=cmd_dump)

    cp = sub.add_parser(
        "capture-phases",
        help="walk a phase-swapped server (calls advance tools per a manifest) and write per-phase dumps",
    )
    _add_source_args(cp, transports=True)
    cp.add_argument("--capture", required=True, help="capture manifest (YAML): phases: [{name, advance: [{tool,args}]}]")
    cp.add_argument("--out-dir", required=True, help="directory to write <phase>.json dumps + phases.yaml")
    cp.set_defaults(func=cmd_capture_phases)

    r = sub.add_parser("report", help="re-render markdown from a stored run")
    r.add_argument("--run", required=True, help="run directory containing report.json")
    r.add_argument("--out", help="write markdown to this path (default: stdout)")
    r.set_defaults(func=cmd_report)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Log the version on startup (stderr, so stdout stays clean for JSON output).
    print(f"mcp-xray {__version__}", file=sys.stderr)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
