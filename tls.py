"""Wspólny kontekst TLS dla pobrań sieciowych: Python z .venv na macOS nie ma
zaufanych certyfikatów systemowych, więc korzystamy z paczki certifi."""

import ssl

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
