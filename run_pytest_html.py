from pathlib import Path
import pytest
import webbrowser

def run_tests_with_html():
    report_dir = Path("pytest_reports")
    report_dir.mkdir(exist_ok=True)

    report_path = report_dir / "report.html"

    # pytest を実行
    ret = pytest.main([
        "--html", str(report_path),
        "--self-contained-html",
    ])

    # レポートをブラウザで開く（pytest の成功/失敗に関係なく開く）
    webbrowser.open(report_path.resolve().as_uri())

    return ret

if __name__ == "__main__":
    raise SystemExit(run_tests_with_html())
