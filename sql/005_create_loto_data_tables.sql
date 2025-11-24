-- nf_loto_final テーブル作成
-- アプリケーション (loto_pg_store.py) から分離されたDDL

CREATE TABLE IF NOT EXISTS nf_loto_final (
    loto        TEXT        NOT NULL,
    num         BIGINT      NOT NULL,
    ds          TIMESTAMP   NOT NULL,
    unique_id   TEXT        NOT NULL,
    y           BIGINT      NOT NULL,
    CO          BIGINT      NOT NULL,
    N1NU        BIGINT,  N1PM BIGINT,
    N2NU        BIGINT,  N2PM BIGINT,
    N3NU        BIGINT,  N3PM BIGINT,
    N4NU        BIGINT,  N4PM BIGINT,
    N5NU        BIGINT,  N5PM BIGINT,
    N6NU        BIGINT,  N6PM BIGINT,
    N7NU        BIGINT,  N7PM BIGINT,
    PRIMARY KEY (loto, num, unique_id)
);

CREATE INDEX IF NOT EXISTS idx_nf_loto_final_ds         ON nf_loto_final(ds);
CREATE INDEX IF NOT EXISTS idx_nf_loto_final_loto_ds    ON nf_loto_final(loto, ds);
CREATE INDEX IF NOT EXISTS idx_nf_loto_final_loto_num   ON nf_loto_final(loto, num);