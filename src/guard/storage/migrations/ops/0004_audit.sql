-- Advice is written once and never changed. A run that finished ok is the record of what the
-- owner was told; only runs that did not finish (paused, stopped, failed) may be retried in place.

CREATE TRIGGER advice_no_update BEFORE UPDATE ON advice
BEGIN SELECT RAISE(ABORT, 'advice is append-only'); END;

CREATE TRIGGER advice_no_delete BEFORE DELETE ON advice
BEGIN SELECT RAISE(ABORT, 'advice is append-only'); END;

CREATE TRIGGER run_ok_no_update BEFORE UPDATE ON run
WHEN OLD.status = 'ok'
BEGIN SELECT RAISE(ABORT, 'an ok run is append-only'); END;

CREATE TRIGGER run_ok_no_delete BEFORE DELETE ON run
WHEN OLD.status = 'ok'
BEGIN SELECT RAISE(ABORT, 'an ok run is append-only'); END;
