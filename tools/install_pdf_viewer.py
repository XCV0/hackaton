"""Подготовка локального просмотрщика PDF. Интернет нужен только при установке."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

VERSION = "6.5.438"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "site" / "vendor" / "pdfjs"
ASSETS = {
    "build/pdf.min.mjs": "3a88402882cfa3e7e72c2c9b5149f52f0f88baf1c5b62aa11a41abdbc2072440",
    "build/pdf.worker.min.mjs": "1e64c8240e54735ec5913b37acd5134747799cb8c6bb3777c70adbc01232d2aa",
    "LICENSE": "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594",
}


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    for asset, expected in ASSETS.items():
        path = TARGET / Path(asset).name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        with urlopen(f"https://cdn.jsdelivr.net/npm/pdfjs-dist@{VERSION}/{asset}", timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Проверка SHA-256 не пройдена: {asset}")
        pending = path.with_suffix(path.suffix + ".part")
        pending.write_bytes(data)
        pending.replace(path)
    print(f"PDF.js {VERSION} установлен: {TARGET}")


if __name__ == "__main__":
    main()
