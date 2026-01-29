"""Display functions for CLI output."""

from kettle.logger import console, log, log_error, log_section, log_success, log_warning


def display_checks(checks: dict[str, dict], title: str = "Results"):
    """Display verification checks with grouped output.

    Args:
        checks: Dictionary mapping check names to dicts with 'verified', 'message', and optional 'critical' keys
        title: Section title to display
    """
    log_section(title)

    passed = []
    failed = []
    warnings = []

    for name, result in checks.items():
        if result["verified"]:
            passed.append((name, result))
        elif not result.get("critical", True):
            warnings.append((name, result))
        else:
            failed.append((name, result))

    # Show passed checks
    if passed:
        console.print("[bold green]Passed:[/bold green]")
        for name, result in passed:
            log_success(f"{name}: {result['message']}")
        console.print()

    # Show warnings (non-critical failures)
    if warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for name, result in warnings:
            log_warning(f"{name}: {result['message']}")
        console.print()

    # Show failures
    if failed:
        console.print("[bold red]Failed:[/bold red]")
        for name, result in failed:
            log_error(f"{name}: {result['message']}")
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
    # Convert to display format with critical flag
    check_results = {}
    for check_name, check_data in checks.items():
        message = check_data["message"]
        is_skip = any(word in message.lower() for word in ["mock", "not implemented", "skipped", "no "])

        check_results[check_name.replace('_', ' ').title()] = {
            "verified": check_data["verified"],
            "message": message,
            "critical": not is_skip,
        }

    display_checks(check_results, title)

    # Check if all critical checks passed
    all_passed = all(
        result["verified"] or not result.get("critical", True)
        for result in check_results.values()
    )

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
            dep_label = f"{name}-{version}" if version else name

            log(f"  • {dep_label}: {r.get('message', '')}", style="dim")
        console.print()

    if failed:
        console.print(f"[red]✗ {len(failed)} dependencies failed verification[/red]")
        for r in failed:
            dep = r.get("dependency", {})
            name = dep.get('name', 'unknown')
            version = dep.get('version', '')
            dep_label = f"{name}-{version}" if version else name

            log_error(f"  {dep_label}: {r.get('message', '')}")
        console.print()
