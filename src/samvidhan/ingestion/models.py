"""Download the local models into the Hugging Face cache (spec: ingestion §3.8, ADR-0008).

Usage: `python -m samvidhan.ingestion.models download [--rerank]` (or `make models`).
Model IDs come from settings (`EMBED_MODEL`, `RERANK_MODEL`), never from code.
"""

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

from samvidhan.core.config import Settings, get_settings
from samvidhan.core.logging import configure_logging, get_logger

# bge-m3 ships an ONNX copy (~2 GB) we never load; skip it.
_IGNORE_PATTERNS = ["onnx/*", "*.onnx"]

log = get_logger(__name__)


def download(model_id: str) -> Path:
    """Fetch `model_id` into the HF cache (no-op when already cached) and return its local path."""
    path = Path(snapshot_download(model_id, ignore_patterns=_IGNORE_PATTERNS))
    size_mb = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6
    log.info("model_downloaded", model=model_id, path=str(path), size_mb=round(size_mb))
    return path


def main(argv: list[str] | None = None, settings: Settings | None = None) -> None:
    parser = argparse.ArgumentParser(prog="samvidhan.ingestion.models")
    sub = parser.add_subparsers(dest="command", required=True)
    dl = sub.add_parser(
        "download", help="download the embedding model (and optionally the reranker)"
    )
    dl.add_argument("--rerank", action="store_true", help="also download RERANK_MODEL")
    args = parser.parse_args(argv)

    settings = settings or get_settings()
    configure_logging(settings)
    download(settings.embed_model)
    if args.rerank:
        download(settings.rerank_model)


if __name__ == "__main__":
    main()
