"""Local web UI — same one-key assistant, no extra vendor account."""

from src.ui.server import DEFAULT_HOST, DEFAULT_PORT, JarvisWebApp, serve

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "JarvisWebApp", "serve"]
