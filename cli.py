#!/usr/bin/env python3
"""CLI + TUI for the global functional resynthesis solver."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich import box

from benchmark import TruthTable, Circuit, Benchmark, load_benchmarks, verify_equivalence, evaluate
from solver import solve


console = Console()


def _ratio_color(ratio: float) -> str:
    if ratio <= 0.5:
        return "bright_green"
    if ratio <= 0.7:
        return "green"
    if ratio <= 0.85:
        return "yellow"
    if ratio < 1.0:
        return "red"
    return "bright_red"


def _build_results_table(results: list[dict], title: str = "Benchmark Results") -> Table:
    table = Table(title=title, box=box.ROUNDED, show_lines=False, pad_edge=True)

    table.add_column("Benchmark", style="bold cyan", no_wrap=True)
    table.add_column("In", justify="right", style="dim")
    table.add_column("Out", justify="right", style="dim")
    table.add_column("Base", justify="right")
    table.add_column("Ours", justify="right", style="bold")
    table.add_column("Saved", justify="right")
    table.add_column("Ratio", justify="right")
    table.add_column("Opt", justify="right", style="dim")
    table.add_column("Gap", justify="right")
    table.add_column("OK", justify="center")
    table.add_column("Time", justify="right", style="dim")

    for r in results:
        ratio = r['reduction_ratio']
        ratio_style = _ratio_color(ratio)
        ratio_str = f"{ratio:.3f}"

        opt_str = str(r['optimal_gates']) if r['optimal_gates'] is not None else "-"

        if r['optimality_gap'] is not None:
            gap = r['optimality_gap']
            gap_str = f"{gap:+.0%}"
            gap_style = "green" if gap <= 0 else "yellow" if gap < 0.5 else "red"
        else:
            gap_str = "-"
            gap_style = "dim"

        ok_str = "[green]Y[/green]" if r['correct'] else "[bold red]N[/bold red]"
        time_str = f"{r['time_s']:.1f}s"

        saved = r['baseline_gates'] - r['gate_count']
        saved_str = f"[green]-{saved}[/green]" if saved > 0 else "[dim]0[/dim]"

        table.add_row(
            r['name'],
            str(r['n_inputs']),
            str(r['n_outputs']),
            str(r['baseline_gates']),
            str(r['gate_count']),
            saved_str,
            f"[{ratio_style}]{ratio_str}[/{ratio_style}]",
            opt_str,
            f"[{gap_style}]{gap_str}[/{gap_style}]",
            ok_str,
            time_str,
        )

    return table


def _build_summary_panel(results: list[dict]) -> Panel:
    avg_ratio = sum(r['reduction_ratio'] for r in results) / len(results)
    total_baseline = sum(r['baseline_gates'] for r in results)
    total_ours = sum(r['gate_count'] for r in results)
    total_saved = total_baseline - total_ours
    all_correct = all(r['correct'] for r in results)
    total_time = sum(r['time_s'] for r in results)

    tier_groups: dict[int, list[float]] = {}
    for r in results:
        tier_groups.setdefault(r['tier'], []).append(r['reduction_ratio'])

    lines = []
    color = _ratio_color(avg_ratio)
    lines.append(f"[bold]Average Reduction Ratio:[/bold]  [{color}]{avg_ratio:.3f}[/{color}]")
    lines.append("")

    for tier in sorted(tier_groups):
        ratios = tier_groups[tier]
        avg = sum(ratios) / len(ratios)
        tc = _ratio_color(avg)
        lines.append(f"  Tier {tier}: [{tc}]{avg:.3f}[/{tc}] ({len(ratios)} benchmarks)")

    lines.append("")
    lines.append(f"[bold]Total Gates:[/bold]  {total_ours} / {total_baseline}  [green](-{total_saved} saved)[/green]")

    correct_str = "[green]Yes[/green]" if all_correct else "[bold red]NO[/bold red]"
    lines.append(f"[bold]All Correct:[/bold]  {correct_str}")
    lines.append(f"[bold]Total Time:[/bold]   {total_time:.1f}s")

    return Panel("\n".join(lines), title="Summary", border_style="blue", padding=(1, 2))


def _build_bar_chart(results: list[dict]) -> Panel:
    max_baseline = max(r['baseline_gates'] for r in results)
    width = 40

    lines = []
    for r in results:
        name = r['name']
        baseline = r['baseline_gates']
        ours = r['gate_count']

        bar_baseline = max(1, int(baseline / max_baseline * width))
        bar_ours = max(1, int(ours / max_baseline * width))

        baseline_bar = "░" * bar_baseline
        ours_bar = "█" * bar_ours

        color = _ratio_color(r['reduction_ratio'])
        lines.append(f"  {name:<14} [{color}]{ours_bar}[/{color}] {ours}")
        lines.append(f"  {'':14} [dim]{baseline_bar}[/dim] [dim]{baseline}[/dim]")

    return Panel("\n".join(lines), title="Gate Counts (solid=ours, light=baseline)", border_style="dim")


def run_benchmarks(
    benchmarks: list[Benchmark],
    filter_name: Optional[str] = None,
    verbose: bool = False,
) -> list[dict]:
    if filter_name:
        matches = [b for b in benchmarks if filter_name.lower() in b.name.lower()]
        if not matches:
            console.print(f"[red]No benchmark matching '{filter_name}'[/red]")
            console.print("Available:", ", ".join(b.name for b in benchmarks))
            sys.exit(1)
        benchmarks = matches

    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Solving...", total=len(benchmarks))

        for bm in benchmarks:
            progress.update(task, description=f"Solving [cyan]{bm.name}[/cyan]...")

            t0 = time.time()
            circuit = solve(bm.truth_table)
            elapsed = time.time() - t0

            r = evaluate(circuit, bm)
            r['time_s'] = round(elapsed, 3)
            results.append(r)

            if verbose:
                ratio = r['reduction_ratio']
                color = _ratio_color(ratio)
                saved = r['baseline_gates'] - r['gate_count']
                progress.console.print(
                    f"  [cyan]{bm.name:<14}[/cyan] "
                    f"{r['baseline_gates']:>3} -> {r['gate_count']:>3}  "
                    f"[{color}]{ratio:.3f}[/{color}]  "
                    f"[green](-{saved})[/green]  "
                    f"[dim]{elapsed:.1f}s[/dim]"
                )

            progress.advance(task)

    return results


def cmd_run(args):
    benchmarks = load_benchmarks()
    results = run_benchmarks(benchmarks, filter_name=args.bench, verbose=args.verbose)

    console.print()
    console.print(_build_results_table(results))
    console.print()
    console.print(_build_summary_panel(results))

    if not args.no_chart and len(results) > 1:
        console.print()
        console.print(_build_bar_chart(results))


def _parse_hex(s: str) -> int:
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    if s.startswith("0b") or s.startswith("0B"):
        return int(s, 2)
    return int(s, 16)


def cmd_solve(args):
    n_inputs = args.inputs
    expected_bits = 1 << n_inputs
    mask = (1 << expected_bits) - 1
    n_chars = max(1, expected_bits // 4)

    tables = []
    for s in args.truth_table:
        tables.append(_parse_hex(s) & mask)

    tt = TruthTable(n_inputs, len(tables), tuple(tables))

    if len(tables) == 1:
        console.print(f"[bold]Solving[/bold] {n_inputs}-input, 1-output function: [cyan]0x{tables[0]:0{n_chars}x}[/cyan]")
    else:
        console.print(f"[bold]Solving[/bold] {n_inputs}-input, {len(tables)}-output function:")
        for j, t in enumerate(tables):
            console.print(f"  out[{j}]: [cyan]0x{t:0{n_chars}x}[/cyan]")

    t0 = time.time()
    circuit = solve(tt)
    elapsed = time.time() - t0

    gc = circuit.gate_count()
    valid = verify_equivalence(circuit, tt)

    console.print()
    ok = "[green]Yes[/green]" if valid else "[bold red]NO[/bold red]"
    console.print(Panel(
        f"[bold]Gates:[/bold]    {gc}\n"
        f"[bold]Depth:[/bold]    {circuit.depth()}\n"
        f"[bold]Outputs:[/bold]  {len(tables)}\n"
        f"[bold]Correct:[/bold]  {ok}\n"
        f"[bold]Time:[/bold]     {elapsed:.2f}s",
        title="Result",
        border_style="green" if valid else "red",
    ))


def cmd_list(args):
    benchmarks = load_benchmarks()

    table = Table(title="Available Benchmarks", box=box.SIMPLE_HEAVY)
    table.add_column("Name", style="cyan")
    table.add_column("Inputs", justify="right")
    table.add_column("Outputs", justify="right")
    table.add_column("Baseline Gates", justify="right")
    table.add_column("Optimal", justify="right", style="dim")
    table.add_column("Category")
    table.add_column("Tier", justify="right")

    for bm in benchmarks:
        opt = str(bm.optimal_gate_count) if bm.optimal_gate_count else "-"
        table.add_row(
            bm.name,
            str(bm.truth_table.n_inputs),
            str(bm.truth_table.n_outputs),
            str(bm.baseline_circuit.gate_count()),
            opt,
            bm.category,
            str(bm.tier),
        )

    console.print(table)


def cmd_info(args):
    benchmarks = load_benchmarks()
    matches = [b for b in benchmarks if args.name.lower() in b.name.lower()]
    if not matches:
        console.print(f"[red]No benchmark matching '{args.name}'[/red]")
        sys.exit(1)

    for bm in matches:
        tt = bm.truth_table
        lines = [
            f"[bold]Name:[/bold]      {bm.name}",
            f"[bold]Category:[/bold]   {bm.category}",
            f"[bold]Tier:[/bold]       {bm.tier}",
            f"[bold]Inputs:[/bold]     {tt.n_inputs}",
            f"[bold]Outputs:[/bold]    {tt.n_outputs}",
            f"[bold]Baseline:[/bold]   {bm.baseline_circuit.gate_count()} gates",
        ]
        if bm.optimal_gate_count:
            lines.append(f"[bold]Optimal:[/bold]    {bm.optimal_gate_count} gates")

        n_chars = max(1, (1 << tt.n_inputs) // 4)
        for j in range(tt.n_outputs):
            hex_str = f"0x{tt.table[j]:0{n_chars}x}"
            if len(hex_str) > 40:
                hex_str = hex_str[:37] + "..."
            lines.append(f"[bold]TT[{j}]:[/bold]      {hex_str}")

        dep_info = []
        for j in range(min(tt.n_outputs, 8)):
            deps = [v for v in range(tt.n_inputs) if tt.depends_on(v, j)]
            dep_info.append(f"  out[{j}] depends on {len(deps)} vars: {deps}")
        if dep_info:
            lines.append(f"[bold]Dependencies:[/bold]")
            lines.extend(dep_info)

        console.print(Panel("\n".join(lines), title=bm.name, border_style="cyan"))


def main():
    parser = argparse.ArgumentParser(
        prog="gfr",
        description="Global Functional Resynthesis Solver",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run solver on benchmarks")
    p_run.add_argument("-b", "--bench", help="Filter benchmarks by name")
    p_run.add_argument("-v", "--verbose", action="store_true", help="Show per-benchmark progress")
    p_run.add_argument("--no-chart", action="store_true", help="Skip the bar chart")
    p_run.set_defaults(func=cmd_run)

    p_solve = sub.add_parser("solve", help="Solve a custom truth table")
    p_solve.add_argument("truth_table", nargs="+", help="Truth table(s) as hex — one per output (e.g. e8 for MAJ3)")
    p_solve.add_argument("-n", "--inputs", type=int, required=True, help="Number of inputs")
    p_solve.set_defaults(func=cmd_solve)

    p_list = sub.add_parser("list", help="List available benchmarks")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", help="Show benchmark details")
    p_info.add_argument("name", help="Benchmark name (partial match)")
    p_info.set_defaults(func=cmd_info)

    args = parser.parse_args()

    if not args.command:
        # Default: run all benchmarks
        args.bench = None
        args.verbose = False
        args.no_chart = False
        cmd_run(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
