"""Report generator — produces console, Markdown, and HTML reports.

Covers all standard load test parameters:
- Test Execution & Run Parameters (VUs, throughput, duration, config)
- Key Performance Indicators (response time, percentiles, error rate, resource usage)
- Charts (latency over time, throughput vs load, error rate, percentiles, CPU/memory)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from tests.load.core.types import LoadTestConfig, LoadTestResult, MessageResult, MetricsSummary


class ReportGenerator:
    """Generates load test reports in multiple formats."""

    def __init__(self, result: LoadTestResult):
        self._result = result
        self._summary = result.summary
        self._config = result.config

    # ─── Console Report (Rich) ────────────────────────────────────────────────

    def print_console_report(self) -> None:
        """Print a rich-formatted report to the console."""
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = Console()
        s = self._summary
        cfg = self._config

        # Header
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]LOAD TEST REPORT[/bold cyan]\n"
                f"[dim]{cfg.worker_name} • {cfg.profile.load_type.value} profile[/dim]",
                border_style="cyan",
            )
        )

        # ── Section 1: Test Execution & Run Parameters ────────────────────────
        console.print("\n[bold yellow]━━━ Test Execution & Run Parameters ━━━[/bold yellow]")
        params_table = Table(show_header=False, box=None, padding=(0, 2))
        params_table.add_column("Param", style="dim")
        params_table.add_column("Value", style="bold")

        start_dt = datetime.fromtimestamp(self._result.start_time, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(self._result.end_time, tz=timezone.utc)

        params_table.add_row("Run ID", cfg.run_id)
        params_table.add_row("Profile", f"{cfg.profile.load_type.value} — {cfg.profile.description}")
        params_table.add_row("Total Messages", str(s.total_messages))
        params_table.add_row("Duration (planned)", f"{cfg.profile.duration_seconds:.1f}s")
        params_table.add_row("Duration (actual)", f"{s.wall_clock_seconds:.1f}s")
        params_table.add_row("Max Concurrent Messages", str(cfg.max_messages))
        params_table.add_row("Start Time", start_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))
        params_table.add_row("End Time", end_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))
        params_table.add_row("Target Topic", cfg.topic_name)
        params_table.add_row("Subscription", cfg.subscription_name)
        env = "Emulator" if cfg.emulator_host else "Live GCP"
        params_table.add_row("Environment", env)
        console.print(params_table)

        # ── Section 2: Key Performance Indicators ─────────────────────────────
        console.print("\n[bold yellow]━━━ Key Performance Indicators (KPIs) ━━━[/bold yellow]")

        kpi_table = Table(show_header=True, header_style="bold magenta")
        kpi_table.add_column("Metric", style="cyan")
        kpi_table.add_column("Value", justify="right")

        kpi_table.add_row("Throughput", f"{s.throughput_msg_per_min:.2f} msg/min")
        kpi_table.add_row("Success", f"{s.successful}/{s.total_messages}")
        kpi_table.add_row("Failed", f"{s.failed}/{s.total_messages}")
        kpi_table.add_row("Error Rate", f"{s.error_rate_pct:.2f}%")
        kpi_table.add_row("", "")
        kpi_table.add_row("[bold]End-to-End Latency[/bold]", "")
        kpi_table.add_row("  p50", f"{s.latency_p50:.3f}s")
        kpi_table.add_row("  p90", f"{s.latency_p90:.3f}s")
        kpi_table.add_row("  p95", f"{s.latency_p95:.3f}s")
        kpi_table.add_row("  p99", f"{s.latency_p99:.3f}s")
        kpi_table.add_row("  max", f"{s.latency_max:.3f}s")
        kpi_table.add_row("  min", f"{s.latency_min:.3f}s")
        kpi_table.add_row("  avg", f"{s.latency_avg:.3f}s")
        kpi_table.add_row("", "")
        kpi_table.add_row("[bold]Processing Time[/bold]", "")
        kpi_table.add_row("  p50", f"{s.processing_p50:.3f}s")
        kpi_table.add_row("  p90", f"{s.processing_p90:.3f}s")
        kpi_table.add_row("  p95", f"{s.processing_p95:.3f}s")
        kpi_table.add_row("  p99", f"{s.processing_p99:.3f}s")
        kpi_table.add_row("  max", f"{s.processing_max:.3f}s")
        kpi_table.add_row("  avg", f"{s.processing_avg:.3f}s")
        kpi_table.add_row("", "")
        kpi_table.add_row("[bold]Queue Wait Time[/bold]", "")
        kpi_table.add_row("  p50", f"{s.queue_wait_p50:.3f}s")
        kpi_table.add_row("  p95", f"{s.queue_wait_p95:.3f}s")
        kpi_table.add_row("  avg", f"{s.queue_wait_avg:.3f}s")
        kpi_table.add_row("", "")
        kpi_table.add_row("[bold]Resource Utilization[/bold]", "")
        kpi_table.add_row("  Memory (peak)", f"{s.memory_peak_mb:.1f} MB")
        kpi_table.add_row("  Memory (avg)", f"{s.memory_avg_mb:.1f} MB")
        console.print(kpi_table)

        # ── Section 3: Stage Breakdown ────────────────────────────────────────
        if s.stage_breakdown:
            console.print("\n[bold yellow]━━━ Processing Stage Breakdown (median) ━━━[/bold yellow]")
            stage_table = Table(show_header=True, header_style="bold green")
            stage_table.add_column("Stage", style="cyan")
            stage_table.add_column("Duration", justify="right")
            stage_table.add_column("% of Total", justify="right")

            total_stage = sum(s.stage_breakdown.values())
            for name, dur in sorted(s.stage_breakdown.items(), key=lambda x: -x[1]):
                pct = (dur / total_stage * 100) if total_stage > 0 else 0
                bar = "█" * int(pct / 2)
                stage_table.add_row(name, f"{dur:.3f}s", f"{pct:.1f}% {bar}")
            console.print(stage_table)

        # ── Section 4: Error Breakdown ────────────────────────────────────────
        if s.error_breakdown:
            console.print("\n[bold yellow]━━━ Error Breakdown ━━━[/bold yellow]")
            err_table = Table(show_header=True, header_style="bold red")
            err_table.add_column("Error Type", style="red")
            err_table.add_column("Count", justify="right")
            for etype, count in sorted(s.error_breakdown.items(), key=lambda x: -x[1]):
                err_table.add_row(etype, str(count))
            console.print(err_table)

        console.print()

    # ─── Markdown Report ──────────────────────────────────────────────────────

    def generate_markdown(self, output_path: Path) -> Path:
        """Generate a Markdown report file."""
        s = self._summary
        cfg = self._config
        start_dt = datetime.fromtimestamp(self._result.start_time, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(self._result.end_time, tz=timezone.utc)

        lines = [
            f"# Load Test Report — {cfg.worker_name}",
            "",
            f"**Run ID:** `{cfg.run_id}`  ",
            f"**Date:** {start_dt.strftime('%Y-%m-%d %H:%M UTC')} → {end_dt.strftime('%H:%M UTC')}  ",
            f"**Profile:** {cfg.profile.load_type.value} — {cfg.profile.description}  ",
            "",
            "---",
            "",
            "## Test Execution & Run Parameters",
            "",
            "| Parameter | Value |",
            "|-----------|-------|",
            f"| Total Messages | {s.total_messages} |",
            f"| Duration (planned) | {cfg.profile.duration_seconds:.1f}s |",
            f"| Duration (actual) | {s.wall_clock_seconds:.1f}s |",
            f"| Max Concurrent Messages | {cfg.max_messages} |",
            f"| Target Topic | `{cfg.topic_name}` |",
            f"| Subscription | `{cfg.subscription_name}` |",
            f"| Environment | {'Emulator' if cfg.emulator_host else 'Live GCP'} |",
            "",
            "---",
            "",
            "## Key Performance Indicators",
            "",
            "### Throughput & Reliability",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Throughput | {s.throughput_msg_per_min:.2f} msg/min |",
            f"| Success | {s.successful}/{s.total_messages} |",
            f"| Failed | {s.failed}/{s.total_messages} |",
            f"| Error Rate | {s.error_rate_pct:.2f}% |",
            "",
            "### End-to-End Latency",
            "",
            "| Percentile | Value |",
            "|-----------|-------|",
            f"| p50 | {s.latency_p50:.3f}s |",
            f"| p90 | {s.latency_p90:.3f}s |",
            f"| p95 | {s.latency_p95:.3f}s |",
            f"| p99 | {s.latency_p99:.3f}s |",
            f"| max | {s.latency_max:.3f}s |",
            f"| min | {s.latency_min:.3f}s |",
            f"| avg | {s.latency_avg:.3f}s |",
            "",
            "### Processing Time",
            "",
            "| Percentile | Value |",
            "|-----------|-------|",
            f"| p50 | {s.processing_p50:.3f}s |",
            f"| p90 | {s.processing_p90:.3f}s |",
            f"| p95 | {s.processing_p95:.3f}s |",
            f"| p99 | {s.processing_p99:.3f}s |",
            f"| max | {s.processing_max:.3f}s |",
            f"| avg | {s.processing_avg:.3f}s |",
            "",
            "### Queue Wait Time",
            "",
            "| Percentile | Value |",
            "|-----------|-------|",
            f"| p50 | {s.queue_wait_p50:.3f}s |",
            f"| p95 | {s.queue_wait_p95:.3f}s |",
            f"| avg | {s.queue_wait_avg:.3f}s |",
            "",
            "### Resource Utilization",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Memory (peak) | {s.memory_peak_mb:.1f} MB |",
            f"| Memory (avg) | {s.memory_avg_mb:.1f} MB |",
            "",
        ]

        if s.stage_breakdown:
            lines += [
                "---",
                "",
                "## Processing Stage Breakdown (median)",
                "",
                "| Stage | Duration | % of Total |",
                "|-------|----------|-----------|",
            ]
            total_stage = sum(s.stage_breakdown.values())
            for name, dur in sorted(s.stage_breakdown.items(), key=lambda x: -x[1]):
                pct = (dur / total_stage * 100) if total_stage > 0 else 0
                lines.append(f"| {name} | {dur:.3f}s | {pct:.1f}% |")
            lines += [""]

        if s.error_breakdown:
            lines += [
                "---",
                "",
                "## Error Breakdown",
                "",
                "| Error Type | Count |",
                "|-----------|-------|",
            ]
            for etype, count in sorted(s.error_breakdown.items(), key=lambda x: -x[1]):
                lines.append(f"| {etype} | {count} |")
            lines += [""]

        output_path = Path(output_path)
        output_path.write_text("\n".join(lines))
        return output_path

    # ─── HTML Report with Charts ──────────────────────────────────────────────

    def generate_html(self, output_path: Path) -> Path:
        """Generate an HTML report with interactive charts (plotly)."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            # Fallback: just write markdown as HTML
            md_path = output_path.with_suffix(".md")
            self.generate_markdown(md_path)
            output_path.write_text(
                f"<pre>{md_path.read_text()}</pre>\n"
                "<p><em>Install plotly for interactive charts: pip install plotly</em></p>"
            )
            return output_path

        messages = self._result.messages
        s = self._summary
        cfg = self._config

        # Prepare time-series data
        successful = [m for m in messages if m.status == "success"]
        timestamps = [
            (m.ack_time or m.processing_end or 0) - self._result.start_time
            for m in successful
            if m.ack_time or m.processing_end
        ]
        latencies = [m.end_to_end_seconds or 0 for m in successful]
        proc_times = [m.processing_seconds or 0 for m in successful]
        memory_vals = [m.memory_mb or 0 for m in messages if m.memory_mb]
        memory_times = [
            (m.ack_time or m.processing_end or 0) - self._result.start_time
            for m in messages
            if m.memory_mb and (m.ack_time or m.processing_end)
        ]

        # Cumulative throughput
        cum_count = list(range(1, len(timestamps) + 1))

        fig = make_subplots(
            rows=3, cols=2,
            subplot_titles=(
                "End-to-End Latency Over Time",
                "Throughput (Cumulative Messages)",
                "Processing Time Distribution",
                "Memory Utilization",
                "Latency Percentiles (p50/p95/p99)",
                "Error Rate Over Time",
            ),
            vertical_spacing=0.1,
        )

        # 1. Latency over time
        fig.add_trace(
            go.Scatter(x=timestamps, y=latencies, mode="markers+lines",
                       name="E2E Latency", marker=dict(size=4)),
            row=1, col=1,
        )

        # 2. Throughput
        fig.add_trace(
            go.Scatter(x=timestamps, y=cum_count, mode="lines",
                       name="Cumulative Messages", line=dict(color="green")),
            row=1, col=2,
        )

        # 3. Processing time histogram
        fig.add_trace(
            go.Histogram(x=proc_times, nbinsx=20, name="Processing Time"),
            row=2, col=1,
        )

        # 4. Memory over time
        if memory_vals:
            fig.add_trace(
                go.Scatter(x=memory_times, y=memory_vals, mode="lines+markers",
                           name="Memory (MB)", line=dict(color="orange")),
                row=2, col=2,
            )

        # 5. Percentile bars
        percentile_names = ["p50", "p90", "p95", "p99", "max"]
        percentile_vals = [
            s.latency_p50, s.latency_p90, s.latency_p95, s.latency_p99, s.latency_max
        ]
        fig.add_trace(
            go.Bar(x=percentile_names, y=percentile_vals, name="Latency Percentiles",
                   marker_color="purple"),
            row=3, col=1,
        )

        # 6. Error rate over time (windowed)
        if messages:
            window = max(1, len(messages) // 10)
            error_rates = []
            error_times = []
            for i in range(0, len(messages), window):
                batch = messages[i:i + window]
                rate = sum(1 for m in batch if m.status == "error") / len(batch) * 100
                t = (batch[-1].publish_time - self._result.start_time)
                error_rates.append(rate)
                error_times.append(t)
            fig.add_trace(
                go.Scatter(x=error_times, y=error_rates, mode="lines+markers",
                           name="Error Rate %", line=dict(color="red")),
                row=3, col=2,
            )

        fig.update_layout(
            height=900,
            title_text=f"Load Test Report — {cfg.worker_name} ({cfg.profile.load_type.value})",
            showlegend=False,
        )

        # Write HTML
        html_content = fig.to_html(full_html=True, include_plotlyjs="cdn")

        # Prepend summary table
        summary_html = self._build_summary_html()
        full_html = html_content.replace(
            "<body>",
            f"<body>{summary_html}",
        )

        output_path = Path(output_path)
        output_path.write_text(full_html)
        return output_path

    def _build_summary_html(self) -> str:
        """Build an HTML summary table to embed in the chart report."""
        s = self._summary
        cfg = self._config
        return f"""
        <div style="font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto;">
        <h2>Summary — {cfg.worker_name}</h2>
        <table style="border-collapse: collapse; width: 100%;">
        <tr><th style="text-align:left; padding:4px; border-bottom:1px solid #ccc;">Metric</th>
            <th style="text-align:right; padding:4px; border-bottom:1px solid #ccc;">Value</th></tr>
        <tr><td>Throughput</td><td style="text-align:right">{s.throughput_msg_per_min:.2f} msg/min</td></tr>
        <tr><td>Success / Total</td><td style="text-align:right">{s.successful} / {s.total_messages}</td></tr>
        <tr><td>Error Rate</td><td style="text-align:right">{s.error_rate_pct:.2f}%</td></tr>
        <tr><td>Latency p95</td><td style="text-align:right">{s.latency_p95:.3f}s</td></tr>
        <tr><td>Processing p95</td><td style="text-align:right">{s.processing_p95:.3f}s</td></tr>
        <tr><td>Memory Peak</td><td style="text-align:right">{s.memory_peak_mb:.1f} MB</td></tr>
        </table>
        </div>
        <hr/>
        """
