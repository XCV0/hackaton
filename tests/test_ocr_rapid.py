import pytest
from app import ocr

rapid = pytest.importorskip("rapidocr_onnxruntime")


def test_rapidocr_reads_rendered_text():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
    except Exception:
        pytest.skip("нет системного шрифта")
    d.text((30, 30), "Recall@5 reaches 0.81 on PubMedQA", fill="black", font=font)
    d.text((30, 110), "Exact match 41.2 percent", fill="black", font=font)
    text = ocr._ocr_rapid(img)
    low = text.lower().replace(" ", "")
    assert "0.81" in text and "pubmedqa" in low and "41.2" in text
