CREATE TABLE raw_payload (
    source_id VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    size_bytes BIGINT NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (source_id, sha256)
);

-- Cleaned, cross-checked series. `sources` lists the independent sources that agreed.
CREATE TABLE market_series (
    series_id VARCHAR NOT NULL,
    obs_date DATE NOT NULL,
    value DECIMAL(28, 10) NOT NULL,
    filled BOOLEAN NOT NULL,
    sources VARCHAR NOT NULL,
    PRIMARY KEY (series_id, obs_date)
);
