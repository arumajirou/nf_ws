import pandas as pd
import pytest

from nf_loto_platform.db import loto_etl


def _build_sample_df():
    return pd.DataFrame(
        {
            "開催回": [1],
            "抽選日": ["2024/01/01"],
            "第1数字": [10],
            "第2数字": [20],
            "キャリーオーバー": [5000],
        }
    )


def _build_numbers_df():
    return pd.DataFrame(
        {
            "抽選日": ["2024-02-01"],
            "開催回": [2],
            "抽選数字": ["123"],
        }
    )


def test_build_long_dataframe_melts_numbers_and_sets_loto(monkeypatch):
    """CSV から取得したデータが long 形式に展開されることを確認。"""

    url_map = {
        "https://example.com/csv/loto6": _build_sample_df(),
        "https://example.com/csv/numbers3": _build_numbers_df(),
    }

    monkeypatch.setattr(loto_etl, "_read_csv_jp", lambda url: url_map[url].copy())

    df = loto_etl.build_long_dataframe(list(url_map))

    assert not df.empty
    assert set(["loto", "開催回", "ds", "unique_id", "y"]).issubset(df.columns)

    loto6_rows = df[df["loto"] == "loto6"]
    assert set(loto6_rows["unique_id"]) == {"N1", "N2"}
    assert loto6_rows["y"].tolist() == [10, 20]

    num3_rows = df[df["loto"] == "num3"]
    assert set(num3_rows["unique_id"]) == {"N1", "N2", "N3"}
    # 123 -> 1,2,3 に展開されている
    assert sorted(num3_rows["y"].tolist()) == [1, 2, 3]


def test_build_long_dataframe_returns_empty_frame_for_no_data(monkeypatch):
    """すべての URL が空 DataFrame を返した場合も安全に空枠を返す。"""

    def _empty_df(_):
        return pd.DataFrame()

    monkeypatch.setattr(loto_etl, "_read_csv_jp", _empty_df)

    df = loto_etl.build_long_dataframe(["https://example.com/csv/loto6"])
    assert list(df.columns) == ["loto", "num", "ds", "unique_id", "y", "CO"]
    assert df.empty


def test_finalize_df_casts_numeric_columns_and_drops_bonus():
    """finalize_df が num/CO/y を数値化し不要列を除去する。"""
    df = pd.DataFrame(
        {
            "loto": ["loto6"],
            "開催回": ["7"],
            "キャリーオーバー": ["100"],
            "ds": pd.to_datetime(["2024-03-01"]),
            "unique_id": ["N1"],
            "y": ["12.5"],
            "N1": ["1"],
            "ボーナス数字": [99],
        }
    )

    finalized = loto_etl.finalize_df(df)

    assert "num" in finalized.columns
    assert "CO" in finalized.columns
    assert "ボーナス数字" not in finalized.columns
    assert finalized["num"].dtype == "int64"
    assert finalized["CO"].dtype == "int64"
    assert pytest.approx(finalized.loc[0, "y"]) == 12.5
    assert pytest.approx(finalized.loc[0, "N1"]) == 1.0
