from pathlib import Path
from services.design import make_graphic

def test_graphic_created(tmp_path):
    out = tmp_path / "test.png"
    make_graphic("Hope in difficult seasons", "Keep trusting and moving forward.", out)
    assert out.exists()
    assert out.stat().st_size > 1000
