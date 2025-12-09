"""Display functions for CLI output."""

from kettle.logger import console, log, log_error, log_section, log_success, log_warning
from kettle.results import CheckResult


def display_checks(checks: dict[str, CheckResult], title: str = "Results"):
    """Display verification checks with grouped output.

    Args:
        checks: Dictionary mapping check names to CheckResult objects
        title: Section title to display
    """
    log_section(title)

    passed = []
    failed = []
    warnings = []

    for name, result in checks.items():
        if result.verified:
            passed.append((name, result))
        elif result.details and not result.details.get("critical", True):
            warnings.append((name, result))
        else:
            failed.append((name, result))

    # Show passed checks
    if passed:
        console.print("[bold green]Passed:[/bold green]")
        for name, result in passed:
            log_success(f"{name}: {result.message}")
        console.print()

    # Show warnings (non-critical failures)
    if warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for name, result in warnings:
            log_warning(f"{name}: {result.message}")
        console.print()

    # Show failures
    if failed:
        console.print("[bold red]Failed:[/bold red]")
        for name, result in failed:
            log_error(f"{name}: {result.message}")
        console.print()

    # Summary
    total = len(checks)
    console.print(
        f"[dim]Total: {total} checks | Passed: {len(passed)} | Failed: {len(failed)} | Warnings: {len(warnings)}[/dim]"
    )
    console.print()


def display_verification_checks(
    checks: dict,
    title: str,
    success_message: str,
    failure_message: str,
) -> bool:
    """Display verification check results in consistent format.

    Args:
        checks: Dict of check results with 'verified' and 'message' keys
        title: Title to display above results
        success_message: Message to show if all checks pass
        failure_message: Message to show if any checks fail

    Returns:
        True if all checks passed, False otherwise
    """
    # Convert dict checks to CheckResult format
    check_results = {}
    for check_name, check_data in checks.items():
        # Determine if this is a warning/skip
        message = check_data["message"]
        is_skip = any(word in message.lower() for word in ["mock", "not implemented", "skipped", "no "])

        check_results[check_name.replace('_', ' ').title()] = CheckResult(
            verified=check_data["verified"],
            message=message,
            details={"critical": not is_skip}
        )

    # Use the display_checks function
    display_checks(check_results, title)

    # Check if all critical checks passed
    all_passed = all(
        result.verified or not result.details.get("critical", True)
        for result in check_results.values()
    )

    # Show final message
    if all_passed:
        log_success(success_message)
    else:
        log_error(failure_message)

    return all_passed


def display_dependency_results(
    results: list[dict], title: str = "Dependency Verification", verbose: bool = False
):
    """Display dependency verification results.

    Args:
        results: List of dependency verification result dictionaries
        title: Section title to display
        verbose: Show detailed hash information
    """
    import hashlib

    log_section(title)

    verified = [r for r in results if r.get("verified")]
    failed = [r for r in results if not r.get("verified")]

    # If verbose, add detailed hash info to results
    if verbose:
        for r in results:
            if r.get("crate_path") and r.get("dependency", {}).get("checksum"):
                actual_hash = hashlib.sha256(r["crate_path"].read_bytes()).hexdigest()
                match = actual_hash == r["dependency"]["checksum"]
                r["message"] += f" | Match: {'✓' if match else '✗'}"

    if verified:
        console.print(f"[green]✓ {len(verified)} dependencies verified[/green]")
        for r in verified:
            dep = r.get("dependency", {})
            name = dep.get('name', 'unknown')
            version = dep.get('version', '')

            # Format: "name-version" if version exists, otherwise just "name"
            dep_label = f"{name}-{version}" if version else name

            log(
                f"  • {dep_label}: {r.get('message', '')}",
                style="dim",
            )
        console.print()

    if failed:
        console.print(f"[red]✗ {len(failed)} dependencies failed verification[/red]")
        for r in failed:
            dep = r.get("dependency", {})
            name = dep.get('name', 'unknown')
            version = dep.get('version', '')

            # Format: "name-version" if version exists, otherwise just "name"
            dep_label = f"{name}-{version}" if version else name

            log_error(
                f"  {dep_label}: {r.get('message', '')}"
            )
        console.print()


def display_build_summary(artifacts: list[dict], title: str = "Build Complete"):
    """Display build results with artifact info.

    Args:
        artifacts: List of build artifact dictionaries
        title: Section title to display
    """
    log_section(title)

    console.print(f"[green]✓ Built {len(artifacts)} artifact(s)[/green]\n")

    for artifact in artifacts:
        name = artifact.get("name", "unknown")
        path = artifact.get("path", "")
        hash_val = artifact.get("hash", "")

        console.print(f"[bold]{name}[/bold]")
        console.print(f"  Path: {path}", style="dim")
        console.print(f"  Hash: {hash_val[:16]}...", style="dim")
        console.print()


def display_attestation_details(report: dict):
    """Display detailed attestation report.

    Args:
        report: Attestation report dictionary
    """
    log_section("Attestation Report Details")

    # Certificate chain
    if "cert_chain" in report:
        console.print("[bold]Certificate Chain:[/bold]")
        for i, cert in enumerate(report["cert_chain"]):
            console.print(f"  [{i}] Subject: {cert.get('subject', 'N/A')}", style="dim")
            console.print(f"      Issuer:  {cert.get('issuer', 'N/A')}", style="dim")
        console.print()

    # Measurement
    if "measurement" in report:
        console.print("[bold]Measurement:[/bold]")
        console.print(f"  {report['measurement']}", style="dim")
        console.print()

    # Report data
    if "report_data" in report:
        console.print("[bold]Report Data:[/bold]")
        console.print(f"  {report['report_data']}", style="dim")
        console.print()

    # Status
    status = report.get("status", "unknown")
    if status == "verified":
        log_success(f"Status: {status}")
    else:
        log_error(f"Status: {status}")
    console.print()


def display_passport_info(passport: dict):
    """Display passport summary information.

    Args:
        passport: Passport dictionary
    """
    log_section("Passport Information")

    # Source info
    source = passport.get("inputs", {}).get("source", {})
    if source:
        console.print("[bold]Source:[/bold]")
        log(f"  Repository: {source.get('repository', 'N/A')}", style="dim")
        log(f"  Commit:     {source.get('commit_hash', 'N/A')[:16]}...", style="dim")
        log(f"  Tree:       {source.get('tree_hash', 'N/A')[:16]}...", style="dim")
        console.print()

    # Toolchain info
    toolchain = passport.get("inputs", {}).get("toolchain", {})
    if toolchain:
        console.print("[bold]Toolchain:[/bold]")
        rustc = toolchain.get("rustc", {})
        cargo = toolchain.get("cargo", {})
        log(f"  rustc: {rustc.get('version', 'N/A')}", style="dim")
        log(f"  cargo: {cargo.get('version', 'N/A')}", style="dim")
        console.print()

    # Build process
    build = passport.get("build_process", {})
    if build:
        console.print("[bold]Build:[/bold]")
        log(f"  Command:   {build.get('command', 'N/A')}", style="dim")
        log(f"  Timestamp: {build.get('timestamp', 'N/A')}", style="dim")
        console.print()
