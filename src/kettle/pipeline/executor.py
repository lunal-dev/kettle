"""
Pipeline executor.

Executes pipeline jobs in dependency order with context management.
"""

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .actions import execute_action
from .context import PipelineContext
from .schema import Job, JobStatus, Pipeline
from .storage import PipelineStorage

console = Console()


def execute_pipeline(
    pipeline: Pipeline,
    storage: Optional[PipelineStorage] = None,
    verbose: bool = False,
) -> str:
    """
    Execute a pipeline synchronously.

    Args:
        pipeline: Pipeline to execute
        storage: Optional storage manager (creates default if not provided)
        verbose: Whether to show verbose output

    Returns:
        Pipeline run ID

    Raises:
        Exception: If pipeline execution fails
    """
    if storage is None:
        storage = PipelineStorage()

    # Create pipeline run
    run_id = storage.create_pipeline_run(pipeline)
    run_dir = storage.get_run_dir(run_id)

    console.print(
        Panel(
            f"[bold cyan]{pipeline.name}[/bold cyan]\n"
            f"Run ID: [yellow]{run_id}[/yellow]\n"
            f"Output: [dim]{run_dir}[/dim]",
            title="Pipeline Execution",
            border_style="cyan",
        )
    )
    console.print()

    # Create context
    context = PipelineContext(pipeline)

    # Get job execution order
    try:
        job_order = pipeline.get_job_order()
    except ValueError as e:
        storage.update_pipeline_status(run_id, "failed", str(e))
        raise

    # Execute jobs in order
    try:
        for job_id in job_order:
            job = pipeline.jobs[job_id]
            _execute_job(job, context, storage, run_id, verbose)

        # Pipeline succeeded
        storage.update_pipeline_status(run_id, "success")
        console.print()
        console.print("[bold green]✓ Pipeline completed successfully[/bold green]")
        console.print(f"[dim]Run ID: {run_id}[/dim]")
        console.print(f"[dim]Output: {run_dir}[/dim]")

    except Exception as e:
        # Pipeline failed
        storage.update_pipeline_status(run_id, "failed", str(e))
        console.print()
        console.print(f"[bold red]✗ Pipeline failed: {e}[/bold red]")
        raise

    return run_id


def _execute_job(
    job: Job,
    context: PipelineContext,
    storage: PipelineStorage,
    run_id: str,
    verbose: bool,
):
    """Execute a single job."""
    # Update status to running
    job.status = JobStatus.RUNNING
    storage.update_job_status(run_id, job)

    console.print(
        f"[bold]→ {job.name}[/bold] [dim]({job.action.value})[/dim]"
    )

    try:
        # Resolve inputs using context
        resolved_inputs = context.resolve_value(job.inputs)

        if verbose:
            console.print(f"[dim]  Inputs: {resolved_inputs}[/dim]")

        # Get job output directory
        job_output_dir = storage.get_job_dir(run_id, job.id)

        # Execute action
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(description="  Executing...", total=None)
            outputs = execute_action(job.action, resolved_inputs, job_output_dir)

        # Validate declared outputs match actual outputs
        if job.outputs:
            for declared_name in job.outputs:
                if declared_name not in outputs:
                    raise ValueError(
                        f"Job '{job.id}' declared output '{declared_name}' but action did not produce it. "
                        f"Actual outputs: {list(outputs.keys())}"
                    )
                # Validate file exists
                output_path = outputs[declared_name].path
                if not output_path.exists():
                    raise ValueError(
                        f"Job '{job.id}' output '{declared_name}' does not exist: {output_path}"
                    )

        # Register outputs in context
        job.resolved_outputs = outputs
        context.register_job_outputs(job.id, outputs)

        # Update status to success
        job.status = JobStatus.SUCCESS
        storage.update_job_status(run_id, job)

        # Print outputs
        if outputs:
            for name, output in outputs.items():
                console.print(f"  [green]✓[/green] {name}: [dim]{output.path}[/dim]")
        else:
            console.print(f"  [green]✓[/green] Completed")

    except Exception as e:
        # Job failed
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        storage.update_job_status(run_id, job, error_message=str(e))

        console.print(f"  [red]✗ Failed: {e}[/red]")
        raise
