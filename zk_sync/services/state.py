"""
Sync state persistence. Mirrors C# Services/SyncStateService.cs.
Tracks which records have been pushed to ERPNext so we can resume on restart.
"""

import logging
import os

log = logging.getLogger(__name__)


def load_state(state, state_file):
    """Load previously pushed record keys from the given state file."""
    if not os.path.exists(state_file):
        return
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    state.pushed_records.add(line)
    except Exception as ex:
        log.warning("Could not load state from %s: %s", state_file, ex)


def save_state(state, state_file):
    """Save pushed record keys to the given state file."""
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            for key in state.pushed_records:
                f.write(key + "\n")
    except Exception as ex:
        log.warning("Could not save state to %s: %s", state_file, ex)
