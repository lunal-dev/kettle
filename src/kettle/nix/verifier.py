"""Verify flake inputs have valid narHash values."""


def verify_flake_input(input_data: dict) -> dict:
    """Verify a single flake input has a narHash.

    We trust Nix's content-addressed store and don't re-verify
    the actual /nix/store paths. We just check that the input
    has a narHash field present in flake.lock.

    Args:
        input_data: Input dictionary from extract_direct_inputs()

    Returns:
        Dict with verification result:
        {
          "input": {...},
          "verified": bool,
          "message": str
        }
    """
    input_name = input_data.get("name", "unknown")
    narHash = input_data.get("narHash")

    if narHash:
        return {
            "input": input_data,
            "verified": True,
            "message": f"narHash present: {narHash[:32]}...",
        }
    else:
        return {
            "input": input_data,
            "verified": False,
            "message": "No narHash found",
        }


def verify_all(inputs: list[dict]) -> list[dict]:
    """Verify all flake inputs.

    Similar to cargo.verify_all() but for Nix inputs.

    Args:
        inputs: List of input dicts from extract_direct_inputs()

    Returns:
        List of verification results
    """
    return [verify_flake_input(input_data) for input_data in inputs]
