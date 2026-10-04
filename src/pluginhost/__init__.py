"""What launcher.pyw provides to the apps it hosts: the first-start install of
the hash-pinned lock, the check of each app's declared dependencies, and the
startup code every app needs (single_instance, error_hook, log_dir).

Owned by the launcher, not by any plugin (docs/adr/0006-plugins-are-self-contained.md).
Imports only the standard library: it runs before any dependency exists.
"""
