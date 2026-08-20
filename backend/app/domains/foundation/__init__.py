"""Foundation domain — tenant, settings, branches, registers.

The package initializer stays side-effect free so models can be reused by
least-privilege workers without importing the HTTP application configuration.
"""
