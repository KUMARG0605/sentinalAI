import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.src.indexer import create_index


def main():
    create_index(
        data_folder=str(PROJECT_ROOT / "resources" / "data"),
        index_path=str(PROJECT_ROOT / "resources" / "faiss_index"),
        exclude_paths=[],
        max_workers=15
    )


if __name__ == "__main__":
    main()
# python -m app.examples.run_indexer