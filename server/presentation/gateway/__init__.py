"""HTTP Gateway boundary for capability operations.

The Gateway is transport-only: it parses operation requests, dispatches them to
capability ports, maps failures to HTTP statuses, and serializes concrete
capability Results. No business or hardware logic lives here.
"""
