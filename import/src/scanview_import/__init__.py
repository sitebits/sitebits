"""scanview Import-Werkzeug."""

from importlib.metadata import version

# Aus den Paket-Metadaten (import/pyproject.toml), die die Release-Kette mit allen anderen
# ausgelieferten Teilprojekten abgleicht (nachweise/versionen.py) — keine zweite Stelle.
__version__ = version("scanview-import")
