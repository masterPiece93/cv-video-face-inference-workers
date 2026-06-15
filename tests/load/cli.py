"""CLI entry point for ECI Workers load testing.

Uses Click for argument parsing and Rich for structured output.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

# Ensure project root is in path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.load.core.analyzer import compute_summary, load_results_from_jsonl
from tests.load.core.metrics import MetricsCollector
from tests.load.core.profiles import RampProfile, SoakProfile, SpikeProfile, StressProfile
from tests.load.core.publisher import LoadPublisher
from tests.load.core.reporter import ReportGenerator
from tests.load.core.types import LoadProfile, LoadTestConfig, LoadTestResult, LoadType
from tests.load.project.config import ECILoadTestSettings
from tests.load.project.data_loader import get_data_loader

console = Console()

LOAD_PROFILES = {
    "ramp": RampProfile,
    "spike": SpikeProfile,
    "soak": SoakProfile,
    "stress": StressProfile,
}


def _make_gcs_source_storage(sa_path: str | None = None):
    """Create a GCPStorageService that reads from REAL GCS.

    Seed data hosted in a ``gs://`` bucket lives in real Cloud Storage, even
    when the rest of a managed run targets the fake-GCS emulator. The
    google-cloud-storage client decides emulator-vs-real at construction time
    based on ``STORAGE_EMULATOR_HOST``, so we temporarily clear that variable
    while building the source client, then restore it.
    """
    from common.services.cloud.gcp.storage import GCPStorageService

    prev = os.environ.pop("STORAGE_EMULATOR_HOST", None)
    try:
        return GCPStorageService(sa_path=sa_path)
    finally:
        if prev is not None:
            os.environ["STORAGE_EMULATOR_HOST"] = prev


def _validate_seed_path(seed: str) -> None:
    """Raise a Click error unless ``seed`` is a gs:// URI or an existing path."""
    from tests.load.project.data_generator import is_gcs_uri

    if is_gcs_uri(seed):
        return
    if not Path(seed).exists():
        raise click.BadParameter(
            f"Seed path does not exist: {seed} "
            "(provide a local directory or a gs://bucket/prefix URI)",
            param_hint="--seed-dir",
        )


@click.group()
@click.version_option(version="1.0.0", prog_name="eci-loadtest")
def cli():
    """ECI Workers Load Testing Tool.

    Run load tests against Pub/Sub-based face encoding and verification workers.
    Supports multiple load profiles: ramp, spike, soak, stress.
    """
    pass


@cli.command()
@click.option(
    "--profile", "-p",
    type=click.Choice(["ramp", "spike", "soak", "stress"]),
    default="ramp",
    help="Load profile type.",
)
@click.option("--messages", "-n", type=int, default=None, help="Total messages to publish.")
@click.option("--duration", "-d", type=float, default=None, help="Test duration in seconds.")
@click.option("--topic", "-t", type=str, default=None, help="Pub/Sub topic name.")
@click.option("--project-id", type=str, default=None, help="GCP project ID.")
@click.option("--emulator", type=str, default=None, help="Pub/Sub emulator host (e.g. localhost:8085).")
@click.option("--data-source", type=click.Choice(["fixtures", "gcs", "generator"]), default=None)
@click.option("--fixtures-dir", type=click.Path(exists=False), default=None)
@click.option("--config", "-c", type=click.Path(exists=True), default=None, help="YAML/JSON config file.")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Results output directory.")
@click.option("--worker", "-w", type=str, default=None, help="Worker name for report labeling.")
def run(profile, messages, duration, topic, project_id, emulator, data_source, fixtures_dir, config, output_dir, worker):
    """Run a load test — publish messages and collect metrics."""
    # Load base settings
    if config:
        cfg_path = Path(config)
        if cfg_path.suffix in (".yml", ".yaml"):
            settings = ECILoadTestSettings.from_yaml(cfg_path)
        else:
            settings = ECILoadTestSettings.from_json(cfg_path)
    else:
        settings = ECILoadTestSettings()

    # CLI overrides
    if topic:
        settings.topic_name = topic
    if project_id:
        settings.project_id = project_id
    if emulator:
        settings.emulator_host = emulator
    if data_source:
        settings.data_source = data_source
    if fixtures_dir:
        settings.fixtures_dir = Path(fixtures_dir)
    if output_dir:
        settings.results_dir = Path(output_dir)
    if worker:
        settings.worker_name = worker

    total_messages = messages or settings.default_total_messages
    test_duration = duration or settings.default_duration_seconds

    # Set emulator env var if specified
    if settings.emulator_host:
        os.environ["PUBSUB_EMULATOR_HOST"] = settings.emulator_host

    # Build profile
    ProfileClass = LOAD_PROFILES[profile]
    load_profile: LoadProfile = ProfileClass(
        total_messages=total_messages,
        duration_seconds=test_duration,
    )

    run_id = f"{settings.worker_name}_{profile}_{int(time.time())}"

    # Build config
    test_config = LoadTestConfig(
        project_id=settings.project_id,
        topic_name=settings.topic_name,
        subscription_name=settings.subscription_name,
        profile=load_profile,
        sa_path=settings.sa_path,
        emulator_host=settings.emulator_host,
        fixtures_dir=settings.fixtures_dir,
        results_dir=settings.results_dir,
        run_id=run_id,
        worker_name=settings.worker_name,
        max_messages=settings.max_messages,
    )

    # Display config
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]ECI Load Test[/bold cyan]\n"
        f"[dim]Profile: {profile} • Messages: {total_messages} • Duration: {test_duration}s[/dim]",
        border_style="cyan",
    ))

    config_table = Table(show_header=False, box=None, padding=(0, 2))
    config_table.add_column("Key", style="dim")
    config_table.add_column("Value", style="bold")
    config_table.add_row("Run ID", run_id)
    config_table.add_row("Topic", settings.topic_name)
    config_table.add_row("Project", settings.project_id)
    config_table.add_row("Environment", "Emulator" if settings.emulator_host else "Live GCP")
    config_table.add_row("Data Source", settings.data_source)
    console.print(config_table)
    console.print()

    # Load data
    loader = get_data_loader(
        source=settings.data_source,
        fixtures_dir=settings.fixtures_dir,
        synthetic_count=total_messages,
    )
    payload_generator = loader.get_payload_generator(total_messages)

    # Publish with progress
    publisher = LoadPublisher(test_config)
    published = []

    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Publishing messages...", total=total_messages)

        def on_publish(index, msg_id, pub_time):
            progress.update(task, advance=1)

        published = publisher.publish_load(
            payload_generator=payload_generator,
            on_publish=on_publish,
        )

    publisher.close()
    end_time = time.time()

    # Save publish manifest
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = settings.results_dir / f"{run_id}_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "profile": profile,
            "total_messages": total_messages,
            "duration_seconds": test_duration,
            "start_time": start_time,
            "end_time": end_time,
            "published": published,
        }, f, indent=2)

    successful_publishes = sum(1 for p in published if p.get("message_id"))
    failed_publishes = sum(1 for p in published if not p.get("message_id"))

    console.print(f"\n[bold green]✓[/bold green] Published {successful_publishes} messages "
                  f"({failed_publishes} failed) in {end_time - start_time:.2f}s")
    console.print(f"  Manifest: {manifest_path}")
    console.print(f"\n[dim]Worker metrics will be collected in: {settings.results_dir}/{run_id}.jsonl[/dim]")
    console.print(f"[dim]Run 'eci-loadtest report {settings.results_dir}/{run_id}.jsonl' after worker processes messages.[/dim]")


@cli.command()
@click.argument("results_file", type=click.Path(exists=True))
@click.option("--format", "-f", "fmt", type=click.Choice(["console", "markdown", "html", "all"]), default="all")
@click.option("--output-dir", "-o", type=click.Path(), default=None)
@click.option("--worker", "-w", type=str, default="face_encoding_worker")
def report(results_file, fmt, output_dir, worker):
    """Generate a load test report from collected results.

    RESULTS_FILE: Path to the .jsonl results file from a load test run.
    """
    results_path = Path(results_file)
    out_dir = Path(output_dir) if output_dir else results_path.parent

    console.print(f"\n[bold]Loading results from:[/bold] {results_path}")

    # Load results
    messages = load_results_from_jsonl(results_path)
    if not messages:
        console.print("[red]No results found in file.[/red]")
        raise SystemExit(1)

    console.print(f"  Loaded {len(messages)} message results")

    # Compute summary
    start_time = min(m.publish_time for m in messages)
    end_time = max(m.ack_time or m.processing_end or m.publish_time for m in messages)
    summary = compute_summary(messages, start_time, end_time)

    # Build result object
    # Use a minimal config for reporting
    config = LoadTestConfig(
        project_id="",
        topic_name="",
        subscription_name="",
        profile=RampProfile(total_messages=len(messages), duration_seconds=end_time - start_time),
        worker_name=worker,
        run_id=results_path.stem,
    )

    result = LoadTestResult(
        config=config,
        messages=messages,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
    )

    reporter = ReportGenerator(result)

    # Generate reports
    if fmt in ("console", "all"):
        reporter.print_console_report()

    if fmt in ("markdown", "all"):
        md_path = out_dir / f"{results_path.stem}_report.md"
        reporter.generate_markdown(md_path)
        console.print(f"[green]✓[/green] Markdown report: {md_path}")

    if fmt in ("html", "all"):
        html_path = out_dir / f"{results_path.stem}_report.html"
        reporter.generate_html(html_path)
        console.print(f"[green]✓[/green] HTML report: {html_path}")


@cli.command()
def profiles():
    """List available load test profiles with descriptions."""
    console.print("\n[bold cyan]Available Load Profiles[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Profile", style="cyan")
    table.add_column("Type")
    table.add_column("Description")
    table.add_column("Use Case", style="dim")

    table.add_row(
        "ramp", "Gradual",
        "Linear ramp-up from 0 to target throughput",
        "Morning traffic surge, gradual scaling",
    )
    table.add_row(
        "spike", "Burst",
        "80% of messages in first 20% of time window",
        "Flash sale, viral event, batch job start",
    )
    table.add_row(
        "soak", "Sustained",
        "Constant rate over extended duration",
        "Memory leaks, connection pool exhaustion",
    )
    table.add_row(
        "stress", "Escalating",
        "5 waves of increasing intensity (1.5x each)",
        "Finding throughput ceiling, saturation point",
    )

    console.print(table)
    console.print()


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), default=None)
def info(config):
    """Show current load test configuration."""
    if config:
        cfg_path = Path(config)
        if cfg_path.suffix in (".yml", ".yaml"):
            settings = ECILoadTestSettings.from_yaml(cfg_path)
        else:
            settings = ECILoadTestSettings.from_json(cfg_path)
    else:
        settings = ECILoadTestSettings()

    console.print("\n[bold cyan]Current Configuration[/bold cyan]\n")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Setting", style="dim")
    table.add_column("Value", style="bold")

    for field_name, field_val in vars(settings).items():
        table.add_row(field_name, str(field_val))

    console.print(table)
    console.print()


# ─── Managed Run (full orchestration) ────────────────────────────────────────


@cli.command()
@click.option(
    "--profile", "-p",
    type=click.Choice(["ramp", "spike", "soak", "stress"]),
    default="ramp",
    help="Load profile type.",
)
@click.option("--messages", "-n", type=int, default=10, help="Total messages to publish.")
@click.option("--duration", "-d", type=float, default=60.0, help="Test duration in seconds.")
@click.option("--worker", "-w", type=str, default="face_encoding_worker", help="Target worker.")
@click.option("--data-source", type=click.Choice(["fixtures", "gcs", "generator"]), default="generator")
@click.option("--seed-dir", type=str, default=None, help="Seed data: a local directory OR a gs://bucket/prefix URI.")
@click.option("--seed-sa-path", type=click.Path(exists=True), default=None, help="Service-account JSON to read a gs:// seed source (defaults to ADC).")
@click.option("--timeout", type=int, default=300, help="Max wait time for processing (seconds).")
@click.option("--output-dir", "-o", type=click.Path(), default="tests/load/results")
@click.option("--encoder-backend", type=click.Choice(["dummy", "face_recognition", "fdetect"]), default="dummy", help="Encoder backend (encoding worker / seed encoding).")
@click.option("--fdetect-channel", type=str, default="", help="fdetect gRPC address (e.g. localhost:7777).")
@click.option("--strategy", type=click.Choice(["with_profile", "common_faces", "profile_match_all", "any_common"]), default="with_profile", help="Verification strategy (verification worker).")
@click.option("--face-tolerance", type=float, default=0.6, help="Face match distance tolerance (verification worker).")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Worker .env file path.")
@click.option("--log-file", type=click.Path(), default=None, help="Path to write worker logs to.")
def managed(profile, messages, duration, worker, data_source, seed_dir, seed_sa_path, timeout, output_dir, encoder_backend, fdetect_channel, strategy, face_tolerance, env_file, log_file):
    """Run a fully managed load test (infra + worker + publish + report).

    Automatically starts Docker Compose emulators, launches an instrumented
    worker, publishes load, waits for completion, generates reports, and
    tears everything down.

    \b
    Example:
        python tests/load/cli.py managed -p ramp -n 20 -d 60 --seed-dir ../candidate_data
        python tests/load/cli.py managed -n 4 --encoder-backend fdetect --fdetect-channel localhost:7777
        python tests/load/cli.py managed -w face_verification_worker -n 4 --seed-dir ../candidate_data \\
            --encoder-backend fdetect --fdetect-channel localhost:7777 --strategy with_profile
        # Seed data hosted in a GCS bucket:
        python tests/load/cli.py managed -n 8 --seed-dir gs://my-bucket/candidate_data \\
            --encoder-backend fdetect --fdetect-channel localhost:7777
    """
    from tests.load.project.orchestrator import LoadTestOrchestrator

    if seed_dir:
        _validate_seed_path(seed_dir)

    results_dir = Path(output_dir)
    run_id = f"{worker}_{profile}_{int(time.time())}"

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]MANAGED LOAD TEST[/bold cyan]\n"
        f"[dim]{worker} • {profile} • {messages} msgs • {duration}s • encoder={encoder_backend}[/dim]",
        border_style="cyan",
    ))

    worker_log = Path(log_file) if log_file else results_dir / f"{run_id}_worker.log"

    # Per-run fixtures dir so encoding/verification payloads never mix.
    seed_fixtures_dir = results_dir / f"{run_id}_fixtures"

    orchestrator = LoadTestOrchestrator(
        worker_name=worker,
        results_dir=results_dir,
        run_id=run_id,
        encoder_backend=encoder_backend,
        fdetect_channel=fdetect_channel,
        env_file=Path(env_file) if env_file else None,
        log_file=worker_log,
        strategy=strategy,
        face_tolerance=face_tolerance,
    )

    try:
        # Step 1: Start infra
        console.print("\n[bold yellow]Step 1:[/bold yellow] Starting infrastructure...")
        orchestrator.start_infra()
        console.print("[green]  ✓ Pub/Sub emulator + fake-GCS ready[/green]")

        # Step 2: Seed data if needed
        if seed_dir:
            console.print(f"\n[bold yellow]Step 2:[/bold yellow] Seeding data from {seed_dir}...")
            _seed_data_for_managed(
                seed_dir=seed_dir,
                target_worker=worker,
                bucket_name="loadtest-bucket",
                emulator_host=orchestrator.infra.gcs_host,
                project_id=orchestrator.infra.project_id,
                fixtures_dir=seed_fixtures_dir,
                encoder_backend=encoder_backend,
                fdetect_channel=fdetect_channel,
                seed_sa_path=seed_sa_path,
            )
            data_source = "fixtures"
            console.print("[green]  ✓ Seed data processed and uploaded[/green]")
        else:
            console.print(f"\n[bold yellow]Step 2:[/bold yellow] Using data source: {data_source}")

        # Step 3: Start instrumented worker
        console.print(f"\n[bold yellow]Step 3:[/bold yellow] Starting instrumented {worker}...")
        os.environ["PUBSUB_EMULATOR_HOST"] = orchestrator.infra.pubsub_host
        os.environ["STORAGE_EMULATOR_HOST"] = orchestrator.infra.gcs_host
        orchestrator.start_worker()
        console.print("[green]  ✓ Worker listening for messages[/green]")
        console.print(f"[dim]    Worker logs → {worker_log}[/dim]")

        # Step 4: Publish load
        console.print(f"\n[bold yellow]Step 4:[/bold yellow] Publishing {messages} messages ({profile})...")

        ProfileClass = LOAD_PROFILES[profile]
        load_profile = ProfileClass(total_messages=messages, duration_seconds=duration)

        test_config = LoadTestConfig(
            project_id=orchestrator.infra.project_id,
            topic_name=orchestrator.ingestion_topic,
            subscription_name=orchestrator.subscription_name,
            profile=load_profile,
            emulator_host=orchestrator.infra.pubsub_host,
            results_dir=results_dir,
            run_id=run_id,
            worker_name=worker,
            max_messages=1,
        )

        loader = get_data_loader(
            source=data_source,
            fixtures_dir=seed_fixtures_dir if seed_dir else Path("tests/load/fixtures"),
            synthetic_count=messages,
        )
        payload_generator = loader.get_payload_generator(messages)

        publisher = LoadPublisher(test_config)
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"),
            BarColumn(), TaskProgressColumn(), console=console,
        ) as progress:
            task = progress.add_task("Publishing...", total=messages)
            published = publisher.publish_load(
                payload_generator=payload_generator,
                on_publish=lambda i, mid, pt: progress.update(task, advance=1),
            )
        publisher.close()
        published_count = sum(1 for p in published if p.get("message_id"))
        console.print(f"[green]  ✓ Published {published_count} messages[/green]")

        # Step 5: Wait for processing
        console.print(f"\n[bold yellow]Step 5:[/bold yellow] Waiting for worker to process (timeout={timeout}s)...")
        completed = orchestrator.wait_for_completion(published_count, timeout=timeout)
        if completed:
            console.print("[green]  ✓ All messages processed[/green]")
        else:
            console.print("[yellow]  ⚠ Timeout — partial results available[/yellow]")

        # Step 6: Stop worker and generate report
        console.print(f"\n[bold yellow]Step 6:[/bold yellow] Generating report...")
        orchestrator.stop_worker()

        jsonl_path = results_dir / f"{run_id}.jsonl"
        if jsonl_path.exists():
            messages_results = load_results_from_jsonl(jsonl_path)
            if messages_results:
                start_t = min(m.publish_time for m in messages_results)
                end_t = max(m.ack_time or m.processing_end or m.publish_time for m in messages_results)
                summary = compute_summary(messages_results, start_t, end_t)

                result = LoadTestResult(
                    config=test_config,
                    messages=messages_results,
                    summary=summary,
                    start_time=start_t,
                    end_time=end_t,
                )

                reporter = ReportGenerator(result)
                reporter.print_console_report()

                md_path = results_dir / f"{run_id}_report.md"
                reporter.generate_markdown(md_path)
                console.print(f"[green]  ✓ Markdown: {md_path}[/green]")

                html_path = results_dir / f"{run_id}_report.html"
                reporter.generate_html(html_path)
                console.print(f"[green]  ✓ HTML: {html_path}[/green]")
            else:
                console.print("[red]  ✗ No results in JSONL file[/red]")
        else:
            console.print(f"[red]  ✗ Results file not found: {jsonl_path}[/red]")

    except Exception as e:
        console.print(f"\n[red bold]Error:[/red bold] {e}")
        raise
    finally:
        # Step 7: Teardown
        console.print(f"\n[bold yellow]Step 7:[/bold yellow] Tearing down...")
        orchestrator.teardown()
        console.print("[green]  ✓ Cleanup complete[/green]\n")


# ─── Data Generator CLI ──────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--seed-dir", "-s",
    type=str,
    required=True,
    help="Seed data: a local candidate_data directory OR a gs://bucket/prefix URI.",
)
@click.option(
    "--target", "-t",
    type=click.Choice(["face_encoding_worker", "face_verification_worker", "onboarding_verification_worker"]),
    default="face_encoding_worker",
    help="Target worker to generate data for.",
)
@click.option("--bucket", type=str, default="loadtest-bucket", help="GCS bucket name.")
@click.option(
    "--storage",
    type=click.Choice(["emulator", "live", "none"]),
    default="none",
    help="Upload to GCS (emulator/live) or skip (none = fixtures only).",
)
@click.option("--emulator-host", type=str, default="http://localhost:5443", help="fake-GCS host.")
@click.option("--fixtures-dir", type=click.Path(), default="tests/load/fixtures", help="Fixtures output dir.")
@click.option("--encoder-backend", type=click.Choice(["face_recognition", "fdetect"]), default="face_recognition")
@click.option("--fdetect-channel", type=str, default="", help="fdetect gRPC address (if using fdetect).")
@click.option("--seed-sa-path", type=click.Path(exists=True), default=None, help="Service-account JSON to read a gs:// seed source (defaults to ADC).")
def generate(seed_dir, target, bucket, storage, emulator_host, fixtures_dir, encoder_backend, fdetect_channel, seed_sa_path):
    """Generate load test data from raw seed videos.

    Processes candidate_data/ videos into the format expected by the target worker.
    For face_verification_worker, this encodes the videos first to produce .npy files.

    \b
    Examples:
        # Generate data for encoding worker (just uploads videos as snippets)
        python tests/load/cli.py generate -s ../candidate_data -t face_encoding_worker

        # Generate data for verification worker (encodes videos → .npy)
        python tests/load/cli.py generate -s ../candidate_data -t face_verification_worker

        # Upload to running fake-GCS emulator
        python tests/load/cli.py generate -s ../candidate_data --storage emulator

        # Read raw seed videos from a GCS bucket and upload to live GCS
        python tests/load/cli.py generate -s gs://my-bucket/candidate_data --storage live
    """
    from tests.load.project.data_generator import LoadTestDataGenerator, is_gcs_uri

    _validate_seed_path(seed_dir)
    seed_is_gcs = is_gcs_uri(seed_dir)

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]Load Test Data Generator[/bold cyan]\n"
        f"[dim]Seed: {seed_dir} → Target: {target}[/dim]",
        border_style="cyan",
    ))

    # Build destination storage service if needed
    storage_service = None
    if storage == "emulator":
        os.environ["STORAGE_EMULATOR_HOST"] = emulator_host
        from common.services.cloud.gcp.storage import GCPStorageService
        storage_service = GCPStorageService()
        console.print(f"  Storage: fake-GCS at {emulator_host}")
    elif storage == "live":
        from common.services.cloud.gcp.storage import GCPStorageService
        storage_service = GCPStorageService()
        console.print("  Storage: Live GCS")
    else:
        console.print("  Storage: None (fixtures only)")

    # Build the SOURCE storage client for a gs:// seed.
    seed_storage = None
    if seed_is_gcs:
        if storage == "live":
            # Source and destination share the same live GCS system.
            seed_storage = storage_service
        else:
            # Read the seed from real GCS even when writing to the emulator.
            seed_storage = _make_gcs_source_storage(seed_sa_path)
        console.print(f"  Seed source: GCS ({seed_dir})")

    # Build encoder if needed for verification targets
    encoder = None
    if target in ("face_verification_worker", "onboarding_verification_worker"):
        from common.services.encoding import get_encoder
        if encoder_backend == "fdetect" and fdetect_channel:
            encoder = get_encoder("fdetect", channel_address=fdetect_channel)
        else:
            encoder = get_encoder("face_recognition", model="hog", num_jitters=1)
        console.print(f"  Encoder: {encoder_backend}")

    # Initialize generator
    generator = LoadTestDataGenerator(
        seed_dir=seed_dir,
        target=target,
        bucket_name=bucket,
        storage_service=storage_service,
        seed_storage=seed_storage,
        encoder=encoder,
    )

    # Scan
    console.print("\n[bold]Scanning seed data...[/bold]")
    candidates = generator.scan_seed_data()
    for c in candidates:
        console.print(f"  📁 {c.email}: {len(c.videos['profile'])} profile, "
                      f"{len(c.videos['interviews'])} interviews")

    # Generate
    console.print(f"\n[bold]Generating data for {target}...[/bold]")
    with console.status("Processing videos..."):
        payloads = generator.generate()

    console.print(f"  Generated {len(payloads)} payloads")

    # Save to fixtures
    fixtures_path = Path(fixtures_dir)
    output_file = generator.save_payloads_to_fixtures(payloads, fixtures_path)
    console.print(f"\n[green]✓[/green] Saved fixtures: {output_file}")

    # Show sample payload
    if payloads:
        console.print("\n[dim]Sample payload:[/dim]")
        console.print_json(json.dumps(payloads[0], indent=2))
    console.print()


def _seed_data_for_managed(
    seed_dir,
    target_worker: str,
    bucket_name: str,
    emulator_host: str,
    project_id: str,
    fixtures_dir: Path,
    encoder_backend: str = "face_recognition",
    fdetect_channel: str = "",
    seed_sa_path: str | None = None,
):
    """Helper: process seed data for a managed run.

    For the encoding worker this just uploads the raw videos as snippets.
    For verification workers it first ENCODES the videos into .npy files,
    which requires a real encoder (face_recognition or fdetect). The `dummy`
    encoder is mapped to face_recognition here because verification needs
    realistic encodings to load from GCS.

    The seed source may be a local directory or a ``gs://bucket/prefix`` URI.
    When it is a GCS URI, the raw videos are read from real Cloud Storage and
    the worker-ready data is written to the fake-GCS emulator.
    """
    from common.services.cloud.gcp.storage import GCPStorageService
    from tests.load.project.data_generator import LoadTestDataGenerator, is_gcs_uri

    # Build the seed SOURCE client first (real GCS), before the emulator host is
    # set, so a gs:// seed source is read from real Cloud Storage.
    seed_storage = None
    if is_gcs_uri(str(seed_dir)):
        seed_storage = _make_gcs_source_storage(seed_sa_path)

    # Destination client → fake-GCS emulator.
    os.environ["STORAGE_EMULATOR_HOST"] = emulator_host
    storage_service = GCPStorageService()
    encoder = None

    if target_worker in ("face_verification_worker", "onboarding_verification_worker"):
        from common.services.encoding import get_encoder
        if encoder_backend == "fdetect" and fdetect_channel:
            encoder = get_encoder("fdetect", channel_address=fdetect_channel)
        else:
            # dummy/face_recognition → use face_recognition for realistic .npy
            encoder = get_encoder("face_recognition", model="hog", num_jitters=1)

    generator = LoadTestDataGenerator(
        seed_dir=seed_dir,
        target=target_worker,
        bucket_name=bucket_name,
        storage_service=storage_service,
        seed_storage=seed_storage,
        encoder=encoder,
    )
    generator.scan_seed_data()
    payloads = generator.generate()
    generator.save_payloads_to_fixtures(payloads, fixtures_dir)


if __name__ == "__main__":
    cli()
