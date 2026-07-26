from pathlib import Path

from raytsystem.documents.index import DocumentIndex


def test_repro(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge" / "manual").mkdir(parents=True)
    (tmp_path / "config" / "raytsystem.toml").write_text(
        """
[documents]
search_page_size = 50

[[documents.roots]]
id = "manual"
path = "knowledge/manual"
mode = "read_write"
kind = "notes"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "knowledge" / "manual" / "Note.md").write_text("# Note\n", encoding="utf-8")
    DocumentIndex(tmp_path).rebuild()
    import gc
    gc.collect()
    (tmp_path / ".raytsystem" / "documents.sqlite").unlink()
    print("UNLINK OK")
