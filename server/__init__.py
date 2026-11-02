"""Protect newly created private files in the Tag Tracker server."""
import os

# Applies to writes by vendored GoogleFindMyTools token-cache code too.
os.umask(0o077)
