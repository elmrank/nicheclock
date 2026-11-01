from nicheclock.cli import main

CFG = """
data_dir: ./data
out_dir: ./out
projects:
  - name: p
    niches:
      - name: n
        keywords: [bird watching]
        sources: [google_trends]
"""


def test_run_refuses_without_identity(tmp_path, capsys):
    f = tmp_path / "nicheclock.yaml"
    f.write_text(CFG)
    assert main(["-c", str(f), "run"]) == 2
    assert "identity" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_doctor_offline(tmp_path, capsys):
    f = tmp_path / "nicheclock.yaml"
    f.write_text(CFG)
    assert main(["-c", str(f), "doctor", "--offline"]) == 1      # identity missing
    out = capsys.readouterr().out
    assert "identity not set" in out and "1 project(s), 1 niche(s) valid" in out
    f.write_text(CFG.replace("data_dir", "identity: {user_agent: 'x/1 (+mailto:a@b.co)', contact_email: a@b.co}\ndata_dir"))
    assert main(["-c", str(f), "doctor", "--offline"]) == 0


def test_missing_config(tmp_path, capsys):
    assert main(["-c", str(tmp_path / "nope.yaml"), "run"]) == 2
    assert "not found" in capsys.readouterr().err


def test_slice_with_missing_optional_dependency_is_reported(monkeypatch, capsys):
    import importlib

    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "nicheclock.mcp_server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake)
    assert main(["mcp"]) == 2
    assert "nicheclock[mcp]" in capsys.readouterr().err
