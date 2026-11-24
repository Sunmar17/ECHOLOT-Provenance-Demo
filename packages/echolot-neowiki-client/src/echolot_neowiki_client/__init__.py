"""Writes ECHOLOT provenance envelopes into NeoWiki.

Independent of both the envelope library (``echolot_prov``) and of Core: this
package only knows how to store an envelope it is handed in a running
NeoWiki/MediaWiki instance. It never builds or validates an envelope, and it
has no base IRI or demo default of its own -- a caller always supplies one.
"""

__version__ = "0.1.0"
