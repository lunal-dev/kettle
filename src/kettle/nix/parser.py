"""Parse flake.lock and extract flake input metadata."""

import json
from pathlib import Path

from kettle.utils import hash_file


def parse_flake_lock(path: Path) -> dict:
    """Parse flake.lock JSON file.

    Args:
        path: Path to flake.lock file

    Returns:
        Dict containing the full flake.lock structure

    Raises:
        FileNotFoundError: If flake.lock doesn't exist
        json.JSONDecodeError: If flake.lock is not valid JSON
    """
    if not path.exists():
        raise FileNotFoundError(f"flake.lock not found: {path}")

    return json.loads(path.read_text())


def hash_flake_lock(path: Path) -> str:
    """Calculate SHA-256 hash of flake.lock file.

    Args:
        path: Path to flake.lock file

    Returns:
        SHA256 hash as hex string
    """
    return hash_file(path)


def extract_direct_inputs(flake_lock_data: dict) -> list[dict]:
    """Extract direct inputs from root node of flake.lock.

    Only returns the direct dependencies listed in root.inputs,
    not transitive dependencies.

    Args:
        flake_lock_data: Parsed flake.lock dictionary

    Returns:
        List of input dictionaries with structure:
        [
          {
            "name": "nixpkgs",
            "type": "github",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "rev": "5ae3b07d8d6527c42f17c876e404993199144b6a",
            "narHash": "sha256-6eeL1YPcY1MV3DDStIDIdy/zZCDKgHdkCmsrLJFiZf0=",
            "lastModified": 1763966396
          },
          ...
        ]
    """
    nodes = flake_lock_data.get("nodes", {})
    root = nodes.get("root", {})
    root_inputs = root.get("inputs", {})

    direct_inputs = []

    for input_name, input_ref in root_inputs.items():
        # input_ref is the node name to look up in nodes
        # Handle case where input_ref might be a string or dict
        if isinstance(input_ref, dict):
            # Some flake.lock versions use {"id": "node-name"}
            input_ref = input_ref.get("id", input_ref)

        # Look up the actual node data
        if input_ref in nodes:
            node = nodes[input_ref]
            locked = node.get("locked", {})

            # Extract all relevant fields from locked section
            input_data = {
                "name": input_name,
                "type": locked.get("type"),
                "narHash": locked.get("narHash"),
            }

            # Add type-specific fields
            if locked.get("type") == "github":
                input_data["owner"] = locked.get("owner")
                input_data["repo"] = locked.get("repo")
                input_data["rev"] = locked.get("rev")
            elif locked.get("type") == "git":
                input_data["url"] = locked.get("url")
                input_data["rev"] = locked.get("rev")
            elif locked.get("type") == "path":
                input_data["path"] = locked.get("path")

            # Optional fields
            if "lastModified" in locked:
                input_data["lastModified"] = locked["lastModified"]

            direct_inputs.append(input_data)

    return direct_inputs
